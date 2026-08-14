#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investor_agent.config import load_env_file  # noqa: E402


load_env_file(ROOT / ".env")

USER_AGENT = os.getenv(
    "SEC_USER_AGENT", "PortfolioLedgerAgent/0.3 (local personal research)"
).strip()
SYMBOLS = ("V", "VTI", "ASML", "TSM")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_error(error: Exception, secrets: list[str]) -> str:
    if isinstance(error, HTTPError):
        text = f"HTTP {error.code}"
        try:
            detail = error.read().decode("utf-8", errors="replace")[:300]
            text += f": {detail}"
        except Exception:
            pass
    elif isinstance(error, URLError):
        text = f"connection: {error.reason}"
    else:
        text = f"{type(error).__name__}: {error}"
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def get_json(
    base_url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    secrets: list[str] | None = None,
) -> tuple[Any | None, str | None]:
    query = urlencode(params or {})
    url = f"{base_url}?{query}" if query else base_url
    request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    try:
        with urlopen(Request(url, headers=request_headers), timeout=25) as response:
            return json.loads(response.read().decode("utf-8")), None
    except (HTTPError, URLError, ValueError, TimeoutError) as error:
        return None, safe_error(error, secrets or [])


def quote_result(
    provider: str,
    symbol: str,
    *,
    price: Any = None,
    currency: Any = None,
    timestamp: Any = None,
    exchange: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    numeric_price = None
    try:
        numeric_price = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        pass
    return {
        "provider": provider,
        "symbol": symbol,
        "ok": numeric_price is not None and numeric_price > 0 and error is None,
        "price": numeric_price,
        "currency": currency,
        "timestamp": timestamp,
        "exchange": exchange,
        "error": error,
    }


def check_twelve_data(key: str) -> dict[str, Any]:
    quotes = []
    for symbol in SYMBOLS:
        payload, error = get_json(
            "https://api.twelvedata.com/quote",
            params={"symbol": symbol, "apikey": key},
            secrets=[key],
        )
        if isinstance(payload, dict) and payload.get("status") == "error":
            error = str(payload.get("message") or payload.get("code") or "API error")
        quotes.append(
            quote_result(
                "twelve_data",
                symbol,
                price=payload.get("close") if isinstance(payload, dict) else None,
                currency=payload.get("currency") if isinstance(payload, dict) else None,
                timestamp=(payload.get("datetime") or payload.get("timestamp")) if isinstance(payload, dict) else None,
                exchange=payload.get("exchange") if isinstance(payload, dict) else None,
                error=error,
            )
        )
    return {"configured": bool(key), "quotes": quotes}


def check_fmp(key: str) -> dict[str, Any]:
    quotes = []
    for symbol in SYMBOLS:
        payload, error = get_json(
            "https://financialmodelingprep.com/stable/quote",
            params={"symbol": symbol, "apikey": key},
            secrets=[key],
        )
        row = payload[0] if isinstance(payload, list) and payload else {}
        if isinstance(payload, dict) and not row:
            error = str(payload.get("Error Message") or payload.get("message") or error or "unexpected response")
        quotes.append(
            quote_result(
                "fmp",
                symbol,
                price=row.get("price"),
                currency=row.get("currency"),
                timestamp=row.get("timestamp"),
                exchange=row.get("exchange"),
                error=error,
            )
        )
    profile, profile_error = get_json(
        "https://financialmodelingprep.com/stable/profile",
        params={"symbol": "V", "apikey": key},
        secrets=[key],
    )
    income, income_error = get_json(
        "https://financialmodelingprep.com/stable/income-statement",
        params={"symbol": "V", "period": "annual", "limit": 1, "apikey": key},
        secrets=[key],
    )
    return {
        "configured": bool(key),
        "quotes": quotes,
        "profile_ok": isinstance(profile, list) and bool(profile),
        "profile_error": profile_error,
        "income_statement_ok": isinstance(income, list) and bool(income),
        "income_statement_error": income_error,
    }


def check_fred(key: str) -> dict[str, Any]:
    series_results = []
    for series_id in ("DGS10", "CPIAUCSL", "CPIAUCNS", "UNRATE"):
        payload, error = get_json(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 12,
            },
            secrets=[key],
        )
        observations = payload.get("observations", []) if isinstance(payload, dict) else []
        latest = next((row for row in observations if row.get("value") not in (None, ".")), {})
        series_results.append(
            {
                "series_id": series_id,
                "ok": bool(latest) and error is None,
                "date": latest.get("date"),
                "value": latest.get("value"),
                "error": error,
            }
        )
    return {"configured": bool(key), "series": series_results}


def check_finnhub(key: str) -> dict[str, Any]:
    quotes = []
    for symbol in SYMBOLS:
        payload, error = get_json(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": key},
            secrets=[key],
        )
        quotes.append(
            quote_result(
                "finnhub",
                symbol,
                price=payload.get("c") if isinstance(payload, dict) else None,
                timestamp=payload.get("t") if isinstance(payload, dict) else None,
                error=error,
            )
        )
    profile, profile_error = get_json(
        "https://finnhub.io/api/v1/stock/profile2",
        params={"symbol": "V", "token": key},
        secrets=[key],
    )
    end = date.today()
    news, news_error = get_json(
        "https://finnhub.io/api/v1/company-news",
        params={
            "symbol": "V",
            "from": (end - timedelta(days=14)).isoformat(),
            "to": end.isoformat(),
            "token": key,
        },
        secrets=[key],
    )
    return {
        "configured": bool(key),
        "quotes": quotes,
        "profile_ok": isinstance(profile, dict) and bool(profile.get("ticker")),
        "profile_error": profile_error,
        "news_ok": isinstance(news, list),
        "news_count": len(news) if isinstance(news, list) else 0,
        "news_error": news_error,
    }


def check_tiingo(token: str) -> dict[str, Any]:
    quotes = []
    auth_headers = {"Authorization": f"Token {token}"}
    for symbol in SYMBOLS:
        payload, error = get_json(
            f"https://api.tiingo.com/tiingo/daily/{symbol}/prices",
            headers=auth_headers,
            secrets=[token],
        )
        row = payload[-1] if isinstance(payload, list) and payload else {}
        quotes.append(
            quote_result(
                "tiingo",
                symbol,
                price=row.get("close"),
                timestamp=row.get("date"),
                error=error,
            )
        )
    fundamentals, fundamentals_error = get_json(
        "https://api.tiingo.com/tiingo/fundamentals/V/statements",
        params={"startDate": "2025-01-01"},
        headers=auth_headers,
        secrets=[token],
    )
    return {
        "configured": bool(token),
        "quotes": quotes,
        "fundamentals_ok": isinstance(fundamentals, list) and bool(fundamentals),
        "fundamentals_error": fundamentals_error,
    }


def search_rows(payload: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        candidates = payload.get("data") or payload.get("result") or []
    else:
        candidates = payload
    if not isinstance(candidates, list):
        return []
    rows = []
    for candidate in candidates[:limit]:
        if not isinstance(candidate, dict):
            continue
        rows.append(
            {
                "symbol": candidate.get("symbol") or candidate.get("ticker"),
                "name": (
                    candidate.get("instrument_name")
                    or candidate.get("description")
                    or candidate.get("name")
                ),
                "exchange": candidate.get("exchange") or candidate.get("exchangeShortName"),
                "type": candidate.get("instrument_type") or candidate.get("type"),
            }
        )
    return rows


def check_international_search(keys: dict[str, str]) -> dict[str, Any]:
    query = "SK Hynix"
    searches: dict[str, dict[str, Any]] = {}

    payload, error = get_json(
        "https://api.twelvedata.com/symbol_search",
        params={"symbol": query, "apikey": keys["twelve_data"]},
        secrets=[keys["twelve_data"]],
    )
    searches["twelve_data"] = {"results": search_rows(payload), "error": error}
    quote, quote_error = get_json(
        "https://api.twelvedata.com/quote",
        params={"symbol": "000660", "exchange": "KRX", "apikey": keys["twelve_data"]},
        secrets=[keys["twelve_data"]],
    )
    searches["twelve_data"]["quote"] = quote_result(
        "twelve_data",
        "000660:KRX",
        price=quote.get("close") if isinstance(quote, dict) else None,
        currency=quote.get("currency") if isinstance(quote, dict) else None,
        timestamp=quote.get("datetime") if isinstance(quote, dict) else None,
        exchange=quote.get("exchange") if isinstance(quote, dict) else None,
        error=quote_error or (quote.get("message") if isinstance(quote, dict) and quote.get("status") == "error" else None),
    )

    payload, error = get_json(
        "https://financialmodelingprep.com/stable/search-name",
        params={"query": query, "apikey": keys["fmp"]},
        secrets=[keys["fmp"]],
    )
    searches["fmp"] = {"results": search_rows(payload), "error": error}
    quote, quote_error = get_json(
        "https://financialmodelingprep.com/stable/quote",
        params={"symbol": "000660.KS", "apikey": keys["fmp"]},
        secrets=[keys["fmp"]],
    )
    row = quote[0] if isinstance(quote, list) and quote else {}
    searches["fmp"]["quote"] = quote_result(
        "fmp",
        "000660.KS",
        price=row.get("price"),
        timestamp=row.get("timestamp"),
        exchange=row.get("exchange"),
        error=quote_error,
    )

    payload, error = get_json(
        "https://finnhub.io/api/v1/search",
        params={"q": query, "token": keys["finnhub"]},
        secrets=[keys["finnhub"]],
    )
    searches["finnhub"] = {"results": search_rows(payload), "error": error}
    quote, quote_error = get_json(
        "https://finnhub.io/api/v1/quote",
        params={"symbol": "000660.KS", "token": keys["finnhub"]},
        secrets=[keys["finnhub"]],
    )
    searches["finnhub"]["quote"] = quote_result(
        "finnhub",
        "000660.KS",
        price=quote.get("c") if isinstance(quote, dict) else None,
        timestamp=quote.get("t") if isinstance(quote, dict) else None,
        error=quote_error,
    )

    payload, error = get_json(
        "https://api.tiingo.com/tiingo/utilities/search",
        params={"query": query},
        headers={"Authorization": f'Token {keys["tiingo"]}'},
        secrets=[keys["tiingo"]],
    )
    searches["tiingo"] = {"results": search_rows(payload), "error": error}
    quote, quote_error = get_json(
        "https://api.tiingo.com/tiingo/daily/SKHY/prices",
        headers={"Authorization": f'Token {keys["tiingo"]}'},
        secrets=[keys["tiingo"]],
    )
    row = quote[-1] if isinstance(quote, list) and quote else {}
    searches["tiingo"]["quote"] = quote_result(
        "tiingo",
        "SKHY",
        price=row.get("close"),
        timestamp=row.get("date"),
        error=quote_error,
    )

    return {"query": query, "providers": searches}


def check_sec() -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT}
    submissions, submissions_error = get_json(
        "https://data.sec.gov/submissions/CIK0001403161.json", headers=headers
    )
    facts, facts_error = get_json(
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0001403161.json", headers=headers
    )
    recent_forms = []
    if isinstance(submissions, dict):
        recent_forms = (submissions.get("filings", {}).get("recent", {}).get("form", []) or [])[:5]
    fact_count = 0
    if isinstance(facts, dict):
        fact_count = sum(len(items) for items in (facts.get("facts") or {}).values())
    return {
        "submissions_ok": isinstance(submissions, dict) and submissions.get("cik") is not None,
        "submissions_error": submissions_error,
        "recent_forms": recent_forms,
        "companyfacts_ok": isinstance(facts, dict) and bool(facts.get("facts")),
        "companyfacts_error": facts_error,
        "taxonomy_concept_count": fact_count,
    }


def check_treasury() -> dict[str, Any]:
    url = (
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
        f"?data=daily_treasury_yield_curve&field_tdr_date_value={date.today().year}"
    )
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml"})
        with urlopen(request, timeout=25) as response:
            root = ElementTree.fromstring(response.read())
        entries = root.findall("{http://www.w3.org/2005/Atom}entry")
        latest = {}
        for entry in entries:
            properties = entry.find(".//{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties")
            if properties is None:
                continue
            row = {node.tag.split("}")[-1]: node.text for node in properties}
            if row.get("BC_10YEAR"):
                latest = row
        return {
            "ok": bool(latest),
            "date": latest.get("NEW_DATE"),
            "ten_year_rate": latest.get("BC_10YEAR"),
            "error": None,
        }
    except (HTTPError, URLError, ElementTree.ParseError, TimeoutError) as error:
        return {"ok": False, "date": None, "ten_year_rate": None, "error": safe_error(error, [])}


def check_bls() -> dict[str, Any]:
    results = []
    for series_id in ("CUUR0000SA0", "LNS14000000"):
        payload, error = get_json(
            f"https://api.bls.gov/publicAPI/v2/timeseries/data/{series_id}"
        )
        series = []
        if isinstance(payload, dict):
            series = payload.get("Results", {}).get("series", []) or []
        latest = (series[0].get("data") or [{}])[0] if series else {}
        results.append(
            {
                "series_id": series_id,
                "ok": bool(latest.get("value")) and error is None,
                "period": f'{latest.get("year", "")}-{latest.get("period", "")}',
                "value": latest.get("value"),
                "error": error,
            }
        )
    return {"mode": "unregistered", "series": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnóstico seguro de proveedores financieros")
    parser.add_argument("--pretty", action="store_true", help="Formatea la salida JSON")
    args = parser.parse_args()

    keys = {
        "twelve_data": os.getenv("TWELVE_DATA_API_KEY", "").strip(),
        "fmp": os.getenv("FMP_API_KEY", "").strip(),
        "fred": os.getenv("FRED_API_KEY", "").strip(),
        "finnhub": os.getenv("FINNHUB_API_KEY", "").strip(),
        "tiingo": os.getenv("TIINGO_API_TOKEN", "").strip(),
    }
    result = {
        "generated_at": utc_now(),
        "scope": {
            "symbols": list(SYMBOLS),
            "purpose": "credential, coverage, freshness and cross-provider consistency check",
        },
        "providers": {
            "twelve_data": check_twelve_data(keys["twelve_data"]),
            "fmp": check_fmp(keys["fmp"]),
            "fred": check_fred(keys["fred"]),
            "finnhub": check_finnhub(keys["finnhub"]),
            "tiingo": check_tiingo(keys["tiingo"]),
        },
        "international_search": check_international_search(keys),
        "official_sources": {
            "sec_edgar": check_sec(),
            "us_treasury": check_treasury(),
            "bls": check_bls(),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
