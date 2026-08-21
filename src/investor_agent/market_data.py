from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote as url_quote
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .analyst_consensus import AnalystConsensusService
from .database import Database


QUOTE_PROVIDER_ORDER = ("twelve_data", "finnhub", "tiingo", "fmp")
PROVIDER_LABELS = {
    "twelve_data": "Twelve Data",
    "finnhub": "Finnhub",
    "tiingo": "Tiingo",
    "fmp": "Financial Modeling Prep",
}


class MarketDataError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def unix_timestamp(value: Any) -> str | None:
    numeric = number(value)
    if not numeric or numeric <= 0:
        return None
    return datetime.fromtimestamp(numeric, timezone.utc).isoformat(timespec="seconds")


def infer_currency(symbol: str, explicit: Any = None) -> str | None:
    if explicit:
        return str(explicit).upper().strip()
    suffixes = {
        ".KS": "KRW",
        ".KQ": "KRW",
        ".L": "GBP",
        ".TO": "CAD",
        ".V": "CAD",
    }
    for suffix, currency in suffixes.items():
        if symbol.endswith(suffix):
            return currency
    return "USD"


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class MarketQuote:
    symbol: str
    provider: str
    price: float
    market_timestamp: str
    retrieved_at: str
    previous_close: float | None = None
    change_amount: float | None = None
    change_percent: float | None = None
    currency: str | None = None
    exchange: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HttpTransport:
    def __init__(self, secrets: list[str] | None = None):
        self.secrets = [value for value in (secrets or []) if value]

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        raw = self.get_text(url, params=params, headers=headers, accept="application/json")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise MarketDataError("La fuente devolvió una respuesta JSON inválida.") from error

    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        accept: str = "text/plain, application/xml",
    ) -> str:
        query = urlencode(params or {})
        target = f"{url}?{query}" if query else url
        request_headers = {
            "Accept": accept,
            "User-Agent": "PortfolioLedgerAgent/0.4 (local personal research)",
        }
        request_headers.update(headers or {})
        try:
            with urlopen(Request(target, headers=request_headers), timeout=18) as response:
                return response.read().decode("utf-8")
        except HTTPError as error:
            try:
                detail = error.read().decode("utf-8", errors="replace")[:280]
            except Exception:
                detail = ""
            message = f"HTTP {error.code}" + (f": {detail}" if detail else "")
            raise MarketDataError(self._redact(message)) from error
        except (URLError, TimeoutError) as error:
            reason = getattr(error, "reason", error)
            raise MarketDataError(self._redact(f"No se pudo conectar: {reason}")) from error

    def _redact(self, message: str) -> str:
        result = message
        for secret in self.secrets:
            result = result.replace(secret, "<redacted>")
        return result


class MarketDataService:
    def __init__(
        self,
        database: Database,
        *,
        keys: dict[str, str] | None = None,
        transport: HttpTransport | None = None,
        quote_ttl_seconds: int = 900,
        official_ttl_seconds: int = 21_600,
        sec_user_agent: str | None = None,
    ):
        self.db = database
        self.keys = keys if keys is not None else {
            "twelve_data": os.getenv("TWELVE_DATA_API_KEY", "").strip(),
            "fmp": os.getenv("FMP_API_KEY", "").strip(),
            "fred": os.getenv("FRED_API_KEY", "").strip(),
            "finnhub": os.getenv("FINNHUB_API_KEY", "").strip(),
            "tiingo": os.getenv("TIINGO_API_TOKEN", "").strip(),
        }
        self.sec_user_agent = (
            sec_user_agent
            if sec_user_agent is not None
            else os.getenv(
                "SEC_USER_AGENT", "PortfolioLedgerAgent/0.4 (local personal research)"
            )
        ).strip()
        self.quote_ttl_seconds = quote_ttl_seconds
        self.official_ttl_seconds = official_ttl_seconds
        self.transport = transport or HttpTransport(list(self.keys.values()))
        self.analysts = AnalystConsensusService(
            database,
            keys=self.keys,
            transport=self.transport,
            ttl_seconds=max(
                3_600,
                int(os.getenv("ANALYST_CONSENSUS_TTL_SECONDS", "86400")),
            ),
        )
        self._sec_tickers: dict[str, dict[str, Any]] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "quote_provider_order": [PROVIDER_LABELS[item] for item in QUOTE_PROVIDER_ORDER],
            "providers": [
                {
                    "id": provider,
                    "name": PROVIDER_LABELS[provider],
                    "configured": bool(self.keys.get(provider)),
                }
                for provider in QUOTE_PROVIDER_ORDER
            ]
            + [
                {
                    "id": "fred",
                    "name": "FRED",
                    "configured": bool(self.keys.get("fred")),
                }
            ],
            "official_sources": [
                {
                    "id": "sec_edgar",
                    "name": "SEC EDGAR",
                    "configured": "@" in self.sec_user_agent,
                    "required_variable": "SEC_USER_AGENT",
                },
                {"id": "us_treasury", "name": "U.S. Treasury", "configured": True},
                {"id": "bls", "name": "BLS", "configured": True},
            ],
            "sec_contact_configured": "@" in self.sec_user_agent,
            "quote_cache_seconds": self.quote_ttl_seconds,
            "analyst_consensus": self.analysts.status(),
        }

    def get_quote(self, symbol: str, *, refresh: bool = False) -> dict[str, Any]:
        clean_symbol = self._validate_symbol(symbol)
        cached = self.db.latest_market_quote(clean_symbol)
        if cached and not refresh and self._is_fresh(cached["retrieved_at"], self.quote_ttl_seconds):
            return {**cached, "cached": True, "stale": False, "warning": None}

        errors = []
        handlers = {
            "twelve_data": self._quote_twelve_data,
            "finnhub": self._quote_finnhub,
            "tiingo": self._quote_tiingo,
            "fmp": self._quote_fmp,
        }
        for provider in QUOTE_PROVIDER_ORDER:
            if not self.keys.get(provider):
                continue
            try:
                quote = handlers[provider](clean_symbol)
                saved = self.db.save_market_quote(quote.to_dict())
                return {**saved, "cached": False, "stale": False, "warning": None}
            except MarketDataError as error:
                errors.append(f"{PROVIDER_LABELS[provider]}: {error}")

        if cached:
            return {
                **cached,
                "cached": True,
                "stale": True,
                "warning": "No se pudo actualizar; se muestra la última cotización guardada.",
            }
        if not any(self.keys.get(provider) for provider in QUOTE_PROVIDER_ORDER):
            raise MarketDataError("No hay proveedores de cotizaciones configurados.")
        raise MarketDataError("; ".join(errors) or "Ningún proveedor devolvió una cotización.")

    def portfolio_valuation(self, *, refresh: bool = False) -> dict[str, Any]:
        positions = self.db.position_costs()
        generated_at = utc_now()
        if not positions:
            return {
                "status": "ready",
                "generated_at": generated_at,
                "reporting_currency": "USD",
                "positions": [],
                "summary": self._empty_summary(),
                "coverage": {"quoted": 0, "total": 0},
                "warnings": [],
                "providers_used": [],
                "currency_note": "Los totales se expresan en USD.",
            }

        rows = []
        warnings: list[str] = []
        providers_used: set[str] = set()
        used_usdc_parity = False
        for position in positions:
            row = {**position}
            try:
                quote = self.get_quote(position["ticker"], refresh=refresh)
                providers_used.add(PROVIDER_LABELS.get(quote["provider"], quote["provider"]))
                quote_currency = quote.get("currency") or "USD"
                compatible = self._currencies_compatible(position["currency"], quote_currency)
                if {position["currency"], quote_currency} == {"USD", "USDC"}:
                    used_usdc_parity = True
                market_value = position["quantity"] * quote["price"]
                daily_change = (
                    position["quantity"] * quote["change_amount"]
                    if quote.get("change_amount") is not None
                    else None
                )
                cost_basis = position.get("cost_basis") if compatible else None
                unrealized = market_value - cost_basis if cost_basis is not None else None
                unrealized_percent = (
                    unrealized / cost_basis * 100
                    if unrealized is not None and cost_basis
                    else None
                )
                row.update(
                    {
                        "quote": self._public_quote(quote),
                        "market_value": market_value,
                        "daily_change": daily_change,
                        "daily_change_percent": quote.get("change_percent"),
                        "unrealized_change": unrealized,
                        "unrealized_change_percent": unrealized_percent,
                        "currency_compatible": compatible,
                        "allocation_percent": None,
                        "error": None,
                    }
                )
                if not compatible:
                    warnings.append(
                        f"{position['ticker']}: costo en {position['currency']} y precio en {quote_currency}; resultado no calculado."
                    )
                if quote.get("warning"):
                    warnings.append(f"{position['ticker']}: {quote['warning']}")
            except MarketDataError as error:
                row.update(
                    {
                        "quote": None,
                        "market_value": None,
                        "daily_change": None,
                        "daily_change_percent": None,
                        "unrealized_change": None,
                        "unrealized_change_percent": None,
                        "currency_compatible": False,
                        "allocation_percent": None,
                        "error": str(error),
                    }
                )
                warnings.append(f"{position['ticker']}: {error}")
            rows.append(row)

        market_value = sum(row["market_value"] or 0 for row in rows)
        for row in rows:
            if row["market_value"] is not None and market_value:
                row["allocation_percent"] = row["market_value"] / market_value * 100

        cost_basis = sum(
            row["cost_basis"] or 0
            for row in rows
            if row["market_value"] is not None and row["currency_compatible"]
        )
        unrealized = sum(
            row["unrealized_change"] or 0
            for row in rows
            if row["unrealized_change"] is not None
        )
        daily_change = sum(
            row["daily_change"] or 0 for row in rows if row["daily_change"] is not None
        )
        previous_market_value = market_value - daily_change
        cash_value = 0.0
        for cash in self.db.latest_cash_balances():
            currency = str(cash["currency"]).upper()
            if currency in {"USD", "USDC"}:
                cash_value += float(cash["amount"])
                used_usdc_parity = used_usdc_parity or currency == "USDC"
            else:
                warnings.append(
                    f"El efectivo en {currency} no se incluyó porque todavía no hay conversión de moneda."
                )

        quoted = sum(1 for row in rows if row["quote"] is not None)
        currency_note = "Los totales se expresan en USD."
        if used_usdc_parity:
            currency_note += " Para comparar operaciones en USDC se usa paridad 1 USDC = 1 USD."
        summary = {
            "market_value": market_value,
            "cash_value": cash_value,
            "portfolio_value": market_value + cash_value,
            "cost_basis": cost_basis,
            "unrealized_change": unrealized,
            "unrealized_change_percent": unrealized / cost_basis * 100 if cost_basis else None,
            "daily_change": daily_change,
            "daily_change_percent": (
                daily_change / previous_market_value * 100 if previous_market_value else None
            ),
        }
        return {
            "status": "ready" if quoted == len(rows) else "partial",
            "generated_at": generated_at,
            "reporting_currency": "USD",
            "positions": rows,
            "summary": summary,
            "coverage": {"quoted": quoted, "total": len(rows)},
            "warnings": list(dict.fromkeys(warnings)),
            "providers_used": sorted(providers_used),
            "currency_note": currency_note,
        }

    def analyst_consensus(
        self,
        symbol: str,
        *,
        refresh: bool = False,
        details: bool = False,
    ) -> dict[str, Any]:
        clean_symbol = self._validate_symbol(symbol)
        quote = self.db.latest_market_quote(clean_symbol)
        if quote is None:
            try:
                quote = self.get_quote(clean_symbol)
            except MarketDataError:
                quote = None
        return self.analysts.consensus(
            clean_symbol,
            current_price=number((quote or {}).get("price")),
            refresh=refresh,
            details=details,
        )

    def portfolio_consensus(self, *, refresh: bool = False) -> dict[str, Any]:
        positions = self.db.positions()
        items = []
        for position in positions:
            symbol = position["ticker"]
            quote = self.db.latest_market_quote(symbol)
            items.append(
                {
                    "ticker": symbol,
                    "quantity": position["quantity"],
                    "consensus": self.analysts.consensus(
                        symbol,
                        current_price=number((quote or {}).get("price")),
                        refresh=refresh,
                        details=False,
                    ),
                }
            )
        return {
            "generated_at": utc_now(),
            "positions": items,
            "coverage": {
                "available": sum(
                    1
                    for item in items
                    if item["consensus"]["status"] in {"ready", "partial"}
                ),
                "total": len(items),
            },
        }

    def macro_snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        cached = self.db.latest_market_snapshot("macro", "us")
        if cached and not refresh and self._is_fresh(cached["retrieved_at"], self.official_ttl_seconds):
            return {**cached["payload"], "cached": True}

        warnings = []
        result: dict[str, Any] = {"generated_at": utc_now(), "series": {}}
        if self.keys.get("fred"):
            for series_id in ("DGS10", "CPIAUCSL", "CPIAUCNS", "UNRATE"):
                try:
                    result["series"][f"fred_{series_id.lower()}"] = self._fred_latest(series_id)
                except MarketDataError as error:
                    warnings.append(f"FRED {series_id}: {error}")
        else:
            warnings.append("FRED no está configurado.")

        try:
            result["series"]["treasury_10y"] = self._treasury_ten_year()
        except MarketDataError as error:
            warnings.append(f"U.S. Treasury: {error}")
        for series_id, key in (("CUUR0000SA0", "bls_cpi_nsa"), ("LNS14000000", "bls_unemployment")):
            try:
                result["series"][key] = self._bls_latest(series_id)
            except MarketDataError as error:
                warnings.append(f"BLS {series_id}: {error}")

        result["status"] = "ready" if not warnings else "partial"
        result["warnings"] = warnings
        result["cached"] = False
        self.db.save_market_snapshot(
            snapshot_type="macro",
            snapshot_key="us",
            provider="FRED+Treasury+BLS",
            as_of=result["generated_at"],
            payload=result,
        )
        return result

    def sec_company_snapshot(self, symbol: str, *, refresh: bool = False) -> dict[str, Any]:
        clean_symbol = self._validate_symbol(symbol)
        if "@" not in self.sec_user_agent:
            raise MarketDataError(
                "Configurá SEC_USER_AGENT con el nombre de la aplicación y un correo real."
            )
        cached = self.db.latest_market_snapshot("sec_company", clean_symbol)
        if cached and not refresh and self._is_fresh(cached["retrieved_at"], self.official_ttl_seconds):
            return {**cached["payload"], "cached": True}

        company = self._sec_company(clean_symbol)
        cik = str(company["cik_str"]).zfill(10)
        headers = {"User-Agent": self.sec_user_agent}
        submissions = self.transport.get_json(
            f"https://data.sec.gov/submissions/CIK{cik}.json", headers=headers
        )
        facts = self.transport.get_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers=headers
        )
        recent = submissions.get("filings", {}).get("recent", {}) if isinstance(submissions, dict) else {}
        filings = []
        forms = recent.get("form") or []
        for index, form in enumerate(forms[:12]):
            accession = (recent.get("accessionNumber") or [None] * len(forms))[index]
            document = (recent.get("primaryDocument") or [None] * len(forms))[index]
            filed = (recent.get("filingDate") or [None] * len(forms))[index]
            archive_url = None
            if accession and document:
                archive_url = (
                    "https://www.sec.gov/Archives/edgar/data/"
                    f"{int(cik)}/{str(accession).replace('-', '')}/{document}"
                )
            filings.append(
                {"form": form, "filed": filed, "accession_number": accession, "url": archive_url}
            )

        payload = {
            "status": "ready",
            "cached": False,
            "generated_at": utc_now(),
            "symbol": clean_symbol,
            "name": company.get("title") or submissions.get("name"),
            "cik": cik,
            "recent_filings": filings,
            "facts": self._selected_sec_facts(facts),
            "source": "SEC EDGAR",
            "warning": None if "@" in self.sec_user_agent else "Falta configurar SEC_USER_AGENT con un correo real.",
        }
        self.db.save_market_snapshot(
            snapshot_type="sec_company",
            snapshot_key=clean_symbol,
            provider="SEC EDGAR",
            as_of=payload["generated_at"],
            payload=payload,
        )
        return payload

    def _quote_twelve_data(self, symbol: str) -> MarketQuote:
        payload = self.transport.get_json(
            "https://api.twelvedata.com/quote",
            params={"symbol": symbol, "apikey": self.keys["twelve_data"]},
        )
        if not isinstance(payload, dict) or payload.get("status") == "error":
            raise MarketDataError(str((payload or {}).get("message") or "Respuesta no interpretable."))
        price = number(payload.get("close"))
        if not price or price <= 0:
            raise MarketDataError("La fuente no devolvió un precio positivo.")
        previous = number(payload.get("previous_close"))
        change = number(payload.get("change"))
        percent = number(payload.get("percent_change"))
        timestamp = str(payload.get("datetime") or unix_timestamp(payload.get("timestamp")) or date.today())
        return MarketQuote(
            symbol=symbol,
            provider="twelve_data",
            price=price,
            previous_close=previous,
            change_amount=change if change is not None else (price - previous if previous else None),
            change_percent=percent,
            currency=infer_currency(symbol, payload.get("currency")),
            exchange=payload.get("exchange"),
            market_timestamp=timestamp,
            retrieved_at=utc_now(),
        )

    def _quote_finnhub(self, symbol: str) -> MarketQuote:
        payload = self.transport.get_json(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": self.keys["finnhub"]},
        )
        price = number(payload.get("c")) if isinstance(payload, dict) else None
        if not price or price <= 0:
            raise MarketDataError("La fuente no devolvió una cotización accesible.")
        previous = number(payload.get("pc"))
        return MarketQuote(
            symbol=symbol,
            provider="finnhub",
            price=price,
            previous_close=previous,
            change_amount=number(payload.get("d")),
            change_percent=number(payload.get("dp")),
            currency=infer_currency(symbol),
            exchange=None,
            market_timestamp=unix_timestamp(payload.get("t")) or utc_now(),
            retrieved_at=utc_now(),
        )

    def _quote_tiingo(self, symbol: str) -> MarketQuote:
        end = date.today()
        payload = self.transport.get_json(
            f"https://api.tiingo.com/tiingo/daily/{url_quote(symbol, safe='.-')}/prices",
            params={"startDate": (end - timedelta(days=10)).isoformat(), "endDate": end.isoformat()},
            headers={"Authorization": f"Token {self.keys['tiingo']}"},
        )
        if not isinstance(payload, list) or not payload:
            raise MarketDataError("La fuente no devolvió cierres diarios.")
        ordered = sorted(payload, key=lambda item: str(item.get("date") or ""))
        latest = ordered[-1]
        price = number(latest.get("close"))
        if not price or price <= 0:
            raise MarketDataError("La fuente no devolvió un precio positivo.")
        previous = number(ordered[-2].get("close")) if len(ordered) > 1 else None
        change = price - previous if previous else None
        return MarketQuote(
            symbol=symbol,
            provider="tiingo",
            price=price,
            previous_close=previous,
            change_amount=change,
            change_percent=change / previous * 100 if change is not None and previous else None,
            currency=infer_currency(symbol),
            exchange=None,
            market_timestamp=str(latest.get("date") or utc_now()),
            retrieved_at=utc_now(),
        )

    def _quote_fmp(self, symbol: str) -> MarketQuote:
        payload = self.transport.get_json(
            "https://financialmodelingprep.com/stable/quote",
            params={"symbol": symbol, "apikey": self.keys["fmp"]},
        )
        row = payload[0] if isinstance(payload, list) and payload else None
        if not isinstance(row, dict):
            raise MarketDataError("La cotización no está incluida en el plan actual.")
        price = number(row.get("price"))
        if not price or price <= 0:
            raise MarketDataError("La fuente no devolvió un precio positivo.")
        previous = number(row.get("previousClose"))
        return MarketQuote(
            symbol=symbol,
            provider="fmp",
            price=price,
            previous_close=previous,
            change_amount=number(row.get("change")),
            change_percent=number(
                row.get("changePercentage")
                if row.get("changePercentage") is not None
                else row.get("changesPercentage")
            ),
            currency=infer_currency(symbol, row.get("currency")),
            exchange=row.get("exchange"),
            market_timestamp=unix_timestamp(row.get("timestamp")) or utc_now(),
            retrieved_at=utc_now(),
        )

    def _fred_latest(self, series_id: str) -> dict[str, Any]:
        payload = self.transport.get_json(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": self.keys["fred"],
                "file_type": "json",
                "sort_order": "desc",
                "limit": 12,
            },
        )
        observations = payload.get("observations", []) if isinstance(payload, dict) else []
        latest = next((item for item in observations if item.get("value") not in (None, ".")), None)
        if not latest:
            raise MarketDataError("No hay una observación reciente.")
        return {
            "source": "FRED",
            "series_id": series_id,
            "date": latest.get("date"),
            "value": number(latest.get("value")),
        }

    def _treasury_ten_year(self) -> dict[str, Any]:
        raw = self.transport.get_text(
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml",
            params={
                "data": "daily_treasury_yield_curve",
                "field_tdr_date_value": date.today().year,
            },
            accept="application/xml",
        )
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as error:
            raise MarketDataError("Treasury devolvió XML inválido.") from error
        rows = []
        for properties in root.findall(
            ".//{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties"
        ):
            row = {node.tag.split("}")[-1]: node.text for node in properties}
            if row.get("NEW_DATE") and row.get("BC_10YEAR"):
                rows.append(row)
        if not rows:
            raise MarketDataError("No se encontró la tasa a 10 años.")
        latest = max(rows, key=lambda item: str(item["NEW_DATE"]))
        return {
            "source": "U.S. Treasury",
            "series_id": "BC_10YEAR",
            "date": latest["NEW_DATE"],
            "value": number(latest["BC_10YEAR"]),
        }

    def _bls_latest(self, series_id: str) -> dict[str, Any]:
        payload = self.transport.get_json(
            f"https://api.bls.gov/publicAPI/v2/timeseries/data/{url_quote(series_id)}"
        )
        series = payload.get("Results", {}).get("series", []) if isinstance(payload, dict) else []
        latest = (series[0].get("data") or [None])[0] if series else None
        if not latest:
            raise MarketDataError("No se encontró una observación reciente.")
        return {
            "source": "BLS",
            "series_id": series_id,
            "date": f"{latest.get('year')}-{latest.get('period')}",
            "value": number(latest.get("value")),
        }

    def _sec_company(self, symbol: str) -> dict[str, Any]:
        if self._sec_tickers is None:
            payload = self.transport.get_json(
                "https://www.sec.gov/files/company_tickers.json",
                headers={"User-Agent": self.sec_user_agent},
            )
            if not isinstance(payload, dict):
                raise MarketDataError("SEC no devolvió el catálogo de emisores.")
            self._sec_tickers = {
                str(item.get("ticker") or "").upper(): item
                for item in payload.values()
                if isinstance(item, dict) and item.get("ticker")
            }
        company = self._sec_tickers.get(symbol)
        if not company:
            raise MarketDataError(f"SEC no encontró un emisor estadounidense para {symbol}.")
        return company

    @classmethod
    def _selected_sec_facts(cls, payload: Any) -> dict[str, Any]:
        facts = payload.get("facts", {}).get("us-gaap", {}) if isinstance(payload, dict) else {}
        concepts = {
            "revenue": (
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
            ),
            "net_income": ("NetIncomeLoss",),
            "diluted_eps": ("EarningsPerShareDiluted",),
            "assets": ("Assets",),
            "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        }
        selected = {}
        for label, candidates in concepts.items():
            for concept in candidates:
                latest = cls._latest_fact(facts.get(concept))
                if latest:
                    selected[label] = {"concept": concept, **latest}
                    break
        return selected

    @staticmethod
    def _latest_fact(concept: Any) -> dict[str, Any] | None:
        units = concept.get("units", {}) if isinstance(concept, dict) else {}
        entries = []
        for unit, values in units.items():
            for value in values or []:
                if value.get("form") in {"10-K", "10-Q"} and value.get("end"):
                    entries.append((unit, value))
        if not entries:
            return None
        unit, latest = max(
            entries,
            key=lambda item: (str(item[1].get("end")), str(item[1].get("filed"))),
        )
        return {
            "value": latest.get("val"),
            "unit": unit,
            "end": latest.get("end"),
            "filed": latest.get("filed"),
            "form": latest.get("form"),
            "fiscal_year": latest.get("fy"),
            "fiscal_period": latest.get("fp"),
        }

    @staticmethod
    def _public_quote(quote: dict[str, Any]) -> dict[str, Any]:
        return {
            key: quote.get(key)
            for key in (
                "symbol",
                "provider",
                "price",
                "previous_close",
                "change_amount",
                "change_percent",
                "currency",
                "exchange",
                "market_timestamp",
                "retrieved_at",
                "cached",
                "stale",
                "warning",
            )
        }

    @staticmethod
    def _validate_symbol(symbol: str) -> str:
        clean = symbol.upper().strip()
        if not re.fullmatch(r"[A-Z0-9.^:-]{1,24}", clean):
            raise MarketDataError("El símbolo de mercado no es válido.")
        return clean

    @staticmethod
    def _currencies_compatible(left: str, right: str) -> bool:
        first = str(left).upper()
        second = str(right).upper()
        return first == second or {first, second} == {"USD", "USDC"}

    @staticmethod
    def _empty_summary() -> dict[str, Any]:
        return {
            "market_value": 0.0,
            "cash_value": 0.0,
            "portfolio_value": 0.0,
            "cost_basis": 0.0,
            "unrealized_change": 0.0,
            "unrealized_change_percent": None,
            "daily_change": 0.0,
            "daily_change_percent": None,
        }

    @staticmethod
    def _is_fresh(retrieved_at: str, ttl_seconds: int) -> bool:
        parsed = parse_datetime(retrieved_at)
        if not parsed:
            return False
        return datetime.now(timezone.utc) - parsed <= timedelta(seconds=ttl_seconds)
