from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from .database import Database


ANALYST_PROVIDER_ORDER = ("fmp", "finnhub", "twelve_data")
PROVIDER_LABELS = {
    "fmp": "Financial Modeling Prep",
    "finnhub": "Finnhub",
    "twelve_data": "Twelve Data",
}
PROVIDER_URLS = {
    "fmp": "https://site.financialmodelingprep.com/developer/docs/stable/price-target-consensus",
    "finnhub": "https://finnhub.io/docs/api/price-target",
    "twelve_data": "https://twelvedata.com/docs",
}
RATING_LABELS = {
    "strong_buy": "Compra fuerte",
    "buy": "Compra",
    "hold": "Mantener",
    "reduce": "Reducir",
    "sell": "Venta",
    "unrated": "Sin consenso",
}
CAPABILITY_LABELS = {
    "target": "precios objetivo",
    "ratings": "recomendaciones agregadas",
    "target_detail": "detalle individual de precios objetivo",
    "grades_detail": "historial individual de recomendaciones",
    "ratings_detail": "detalle individual de recomendaciones",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def integer(value: Any) -> int:
    numeric = number(value)
    return max(0, int(numeric)) if numeric is not None else 0


def first_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return next((item for item in payload if isinstance(item, dict)), {})
    if isinstance(payload, dict):
        for key in ("data", "result", "results"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return first_dict(nested)
            if isinstance(nested, dict):
                return nested
        return payload
    return {}


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "result", "results", "ratings", "trends"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [payload] if payload else []
    return []


def normalize_rating(value: Any) -> str:
    clean = re.sub(r"[^a-z]+", " ", str(value or "").lower()).strip()
    if not clean:
        return "unrated"
    if any(term in clean for term in ("strong sell", "conviction sell")):
        return "sell"
    if any(term in clean for term in ("strong buy", "conviction buy", "top pick")):
        return "strong_buy"
    if any(term in clean for term in ("sell", "underperform", "underweight", "reduce")):
        return "reduce" if not clean.startswith("sell") else "sell"
    if any(term in clean for term in ("buy", "outperform", "overweight", "accumulate", "positive")):
        return "buy"
    if any(term in clean for term in ("hold", "neutral", "equal weight", "market perform", "peer perform")):
        return "hold"
    return "unrated"


def rating_distribution(payload: dict[str, Any]) -> dict[str, Any] | None:
    distribution = {
        "strong_buy": integer(payload.get("strongBuy", payload.get("strong_buy"))),
        "buy": integer(payload.get("buy")),
        "hold": integer(payload.get("hold")),
        "sell": integer(payload.get("sell")),
        "strong_sell": integer(payload.get("strongSell", payload.get("strong_sell"))),
    }
    total = sum(distribution.values())
    raw_consensus = payload.get("consensus") or payload.get("recommendation") or payload.get("rating")
    code = normalize_rating(raw_consensus)
    score = None
    if total:
        score = (
            2 * distribution["strong_buy"]
            + distribution["buy"]
            - distribution["sell"]
            - 2 * distribution["strong_sell"]
        ) / total
        if code == "unrated":
            if score >= 1.2:
                code = "strong_buy"
            elif score >= 0.35:
                code = "buy"
            elif score > -0.35:
                code = "hold"
            elif score > -1.2:
                code = "reduce"
            else:
                code = "sell"
    if total == 0 and code == "unrated":
        return None
    return {
        **distribution,
        "total": total,
        "score": round(score, 3) if score is not None else None,
        "raw_consensus": str(raw_consensus) if raw_consensus else None,
        "label_code": code,
        "label": RATING_LABELS[code],
    }


class AnalystConsensusService:
    def __init__(
        self,
        database: Database,
        *,
        keys: dict[str, str],
        transport: Any,
        ttl_seconds: int = 86_400,
    ):
        self.db = database
        self.keys = keys
        self.transport = transport
        self.ttl_seconds = ttl_seconds

    def status(self) -> dict[str, Any]:
        return {
            "provider_order": [PROVIDER_LABELS[item] for item in ANALYST_PROVIDER_ORDER],
            "cache_seconds": self.ttl_seconds,
            "providers": [
                {
                    "id": provider,
                    "name": PROVIDER_LABELS[provider],
                    "configured": bool(self.keys.get(provider)),
                    "url": PROVIDER_URLS[provider],
                }
                for provider in ANALYST_PROVIDER_ORDER
            ],
        }

    def consensus(
        self,
        symbol: str,
        *,
        current_price: float | None = None,
        refresh: bool = False,
        details: bool = False,
    ) -> dict[str, Any]:
        clean_symbol = self._validate_symbol(symbol)
        providers = [
            self._provider(clean_symbol, provider, refresh=refresh, details=details)
            for provider in ANALYST_PROVIDER_ORDER
        ]
        target_provider = next(
            (provider for provider in providers if provider.get("target")), None
        )
        rating_provider = next(
            (provider for provider in providers if provider.get("ratings")), None
        )
        target = dict(target_provider["target"]) if target_provider else None
        ratings = dict(rating_provider["ratings"]) if rating_provider else None
        selected_target = None
        upside_percent = None
        if target:
            selected_target = target.get("median") or target.get("mean")
            if current_price and selected_target:
                upside_percent = (selected_target / current_price - 1) * 100
        available = [provider for provider in providers if provider["status"] == "ready"]
        configured = [provider for provider in providers if provider["configured"]]
        if target and ratings:
            status = "ready"
        elif target or ratings:
            status = "partial"
        elif configured and all(provider["status"] == "no_coverage" for provider in configured):
            status = "no_coverage"
        else:
            status = "unavailable"

        actions = []
        for provider in providers:
            actions.extend(provider.get("actions") or [])
        actions.sort(key=lambda item: item.get("date") or "", reverse=True)
        warnings = []
        for provider in providers:
            if provider["configured"] and provider["status"] in {"restricted", "unavailable"}:
                warnings.append(
                    f"{provider['name']}: {provider.get('message') or 'datos no disponibles'}."
                )

        return {
            "symbol": clean_symbol,
            "status": status,
            "generated_at": utc_now(),
            "current_price": current_price,
            "target": target,
            "selected_target": selected_target,
            "target_provider": target_provider["id"] if target_provider else None,
            "ratings": ratings,
            "rating_provider": rating_provider["id"] if rating_provider else None,
            "upside_percent": upside_percent,
            "provider_coverage": {
                "available": len(available),
                "configured": len(configured),
                "total": len(providers),
            },
            "providers": providers,
            "actions": actions if details else [],
            "action_count": len(actions),
            "warnings": warnings,
            "disclaimer": (
                "El consenso refleja fuentes externas y no es una recomendación personal "
                "ni un precio de salida garantizado."
            ),
        }

    def _provider(
        self, symbol: str, provider: str, *, refresh: bool, details: bool
    ) -> dict[str, Any]:
        snapshot_type = "analyst_consensus_detail" if details else "analyst_consensus"
        cached = self.db.latest_market_snapshot_for_provider(
            snapshot_type, symbol, provider
        )
        if cached and not refresh and self._is_fresh(cached["retrieved_at"]):
            return {**cached["payload"], "cached": True}
        if not self.keys.get(provider):
            return self._base_provider(
                provider,
                status="unconfigured",
                message="falta configurar la credencial",
                configured=False,
            )

        handler = {
            "fmp": self._fmp,
            "finnhub": self._finnhub,
            "twelve_data": self._twelve_data,
        }[provider]
        payload = handler(symbol, details=details)
        self.db.save_market_snapshot(
            snapshot_type=snapshot_type,
            snapshot_key=symbol,
            provider=provider,
            as_of=payload.get("as_of") or date.today().isoformat(),
            payload=payload,
        )
        return {**payload, "cached": False}

    def _fmp(self, symbol: str, *, details: bool) -> dict[str, Any]:
        result = self._base_provider("fmp")
        target_payload = self._request(
            "fmp",
            "target",
            "https://financialmodelingprep.com/stable/price-target-consensus",
            {"symbol": symbol, "apikey": self.keys["fmp"]},
            result,
        )
        target_row = first_dict(target_payload)
        target = self._target(
            low=target_row.get("targetLow"),
            median=target_row.get("targetMedian"),
            mean=target_row.get("targetConsensus"),
            high=target_row.get("targetHigh"),
            analyst_count=target_row.get("numberAnalysts"),
        )
        if target:
            result["target"] = target
            result["as_of"] = target.get("last_updated") or result["as_of"]

        ratings_payload = self._request(
            "fmp",
            "ratings",
            "https://financialmodelingprep.com/stable/grades-consensus",
            {"symbol": symbol, "apikey": self.keys["fmp"]},
            result,
        )
        ratings = rating_distribution(first_dict(ratings_payload))
        if ratings:
            result["ratings"] = ratings

        if details:
            grades_payload = self._request(
                "fmp",
                "grades_detail",
                "https://financialmodelingprep.com/stable/grades",
                {"symbol": symbol, "apikey": self.keys["fmp"]},
                result,
            )
            result["actions"].extend(
                self._fmp_grade_action(row) for row in rows(grades_payload)
            )
            targets_payload = self._request(
                "fmp",
                "target_detail",
                "https://financialmodelingprep.com/stable/price-target-news",
                {"symbol": symbol, "page": 0, "limit": 100, "apikey": self.keys["fmp"]},
                result,
            )
            result["actions"].extend(
                self._fmp_target_action(row) for row in rows(targets_payload)
            )
        return self._finish_provider(result)

    def _finnhub(self, symbol: str, *, details: bool) -> dict[str, Any]:
        result = self._base_provider("finnhub")
        recommendations = self._request(
            "finnhub",
            "ratings",
            "https://finnhub.io/api/v1/stock/recommendation",
            {"symbol": symbol, "token": self.keys["finnhub"]},
            result,
        )
        recommendation_rows = rows(recommendations)
        recommendation_rows.sort(key=lambda row: str(row.get("period") or ""), reverse=True)
        latest = recommendation_rows[0] if recommendation_rows else {}
        ratings = rating_distribution(latest)
        if ratings:
            result["ratings"] = ratings
            result["as_of"] = latest.get("period") or result["as_of"]
        if details:
            result["rating_history"] = [
                {"period": row.get("period"), **(rating_distribution(row) or {})}
                for row in recommendation_rows
            ]

        target_payload = self._request(
            "finnhub",
            "target",
            "https://finnhub.io/api/v1/stock/price-target",
            {"symbol": symbol, "token": self.keys["finnhub"]},
            result,
        )
        target_row = first_dict(target_payload)
        target = self._target(
            low=target_row.get("targetLow"),
            median=target_row.get("targetMedian"),
            mean=target_row.get("targetMean"),
            high=target_row.get("targetHigh"),
            analyst_count=target_row.get("numberAnalysts"),
            last_updated=target_row.get("lastUpdated"),
        )
        if target:
            result["target"] = target
            result["as_of"] = target.get("last_updated") or result["as_of"]
        return self._finish_provider(result)

    def _twelve_data(self, symbol: str, *, details: bool) -> dict[str, Any]:
        result = self._base_provider("twelve_data")
        target_payload = self._request(
            "twelve_data",
            "target",
            "https://api.twelvedata.com/price_target",
            {"symbol": symbol, "apikey": self.keys["twelve_data"]},
            result,
        )
        target_row = first_dict(target_payload)
        target = self._target(
            low=self._pick(target_row, "low", "target_low", "targetLow"),
            median=self._pick(target_row, "median", "target_median", "targetMedian"),
            mean=self._pick(target_row, "average", "mean", "target_mean", "targetMean"),
            high=self._pick(target_row, "high", "target_high", "targetHigh"),
            analyst_count=self._pick(target_row, "analyst_count", "number_analysts", "numberAnalysts"),
            last_updated=self._pick(target_row, "last_updated", "updated_at", "date"),
        )
        if target:
            result["target"] = target
            result["as_of"] = target.get("last_updated") or result["as_of"]

        recommendation_payload = self._request(
            "twelve_data",
            "ratings",
            "https://api.twelvedata.com/recommendations",
            {"symbol": symbol, "apikey": self.keys["twelve_data"]},
            result,
        )
        recommendation_rows = rows(recommendation_payload)
        latest = recommendation_rows[0] if recommendation_rows else first_dict(recommendation_payload)
        ratings = rating_distribution(latest)
        if ratings:
            result["ratings"] = ratings

        if details:
            detail_payload = self._request(
                "twelve_data",
                "ratings_detail",
                "https://api.twelvedata.com/analyst_ratings/light",
                {"symbol": symbol, "apikey": self.keys["twelve_data"]},
                result,
            )
            result["actions"].extend(
                self._twelve_action(row) for row in rows(detail_payload)
            )
        return self._finish_provider(result)

    def _request(
        self,
        provider: str,
        capability: str,
        url: str,
        params: dict[str, Any],
        result: dict[str, Any],
    ) -> Any:
        try:
            payload = self.transport.get_json(url, params=params)
        except RuntimeError as error:
            message = self._friendly_error(str(error))
            result["limitations"].append(
                f"{CAPABILITY_LABELS.get(capability, capability)}: {message}"
            )
            return None
        if isinstance(payload, dict) and (
            payload.get("status") == "error" or payload.get("Error Message")
        ):
            raw = payload.get("message") or payload.get("Error Message") or "respuesta inválida"
            message = self._friendly_error(str(raw))
            result["limitations"].append(
                f"{CAPABILITY_LABELS.get(capability, capability)}: {message}"
            )
            return None
        return payload

    @staticmethod
    def _friendly_error(message: str) -> str:
        if re.search(r"HTTP (?:402|403)", message):
            return "el plan actual no incluye este conjunto de datos"
        if "HTTP 429" in message:
            return "límite temporal de consultas alcanzado"
        if "HTTP 401" in message:
            return "credencial rechazada por el proveedor"
        return "la fuente no respondió correctamente"

    @staticmethod
    def _target(
        *,
        low: Any,
        median: Any,
        mean: Any,
        high: Any,
        analyst_count: Any = None,
        last_updated: Any = None,
    ) -> dict[str, Any] | None:
        result = {
            "low": number(low),
            "median": number(median),
            "mean": number(mean),
            "high": number(high),
            "analyst_count": integer(analyst_count) or None,
            "last_updated": str(last_updated) if last_updated else None,
            "currency": "USD",
        }
        return result if any(result[key] is not None for key in ("low", "median", "mean", "high")) else None

    @staticmethod
    def _fmp_grade_action(row: dict[str, Any]) -> dict[str, Any]:
        code = normalize_rating(row.get("newGrade"))
        raw_action = str(row.get("action") or "").lower().strip()
        action = {
            "maintain": "Mantiene",
            "reiterate": "Reitera",
            "reiterated": "Reitera",
            "upgrade": "Mejora",
            "downgrade": "Reduce",
            "initiate": "Inicia cobertura",
            "initiated": "Inicia cobertura",
        }.get(raw_action, row.get("action"))
        return {
            "type": "rating",
            "provider": "fmp",
            "date": row.get("date"),
            "firm": row.get("gradingCompany"),
            "action": action,
            "rating_from": row.get("previousGrade"),
            "rating_to": row.get("newGrade"),
            "normalized_rating": code,
            "normalized_label": RATING_LABELS[code],
            "price_target": None,
            "price_when_posted": None,
            "title": None,
            "url": "https://site.financialmodelingprep.com/developer/docs/stable/grades",
        }

    @staticmethod
    def _fmp_target_action(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "price_target",
            "provider": "fmp",
            "date": row.get("publishedDate") or row.get("date"),
            "firm": row.get("analystCompany") or row.get("analystName"),
            "action": "Precio objetivo",
            "rating_from": None,
            "rating_to": None,
            "normalized_rating": "unrated",
            "normalized_label": RATING_LABELS["unrated"],
            "price_target": number(row.get("priceTarget")),
            "price_when_posted": number(row.get("priceWhenPosted")),
            "title": row.get("newsTitle") or row.get("title"),
            "url": row.get("newsURL") or row.get("url") or PROVIDER_URLS["fmp"],
        }

    @staticmethod
    def _twelve_action(row: dict[str, Any]) -> dict[str, Any]:
        rating = AnalystConsensusService._pick(
            row, "rating", "rating_current", "new_rating", "recommendation"
        )
        code = normalize_rating(rating)
        return {
            "type": "rating",
            "provider": "twelve_data",
            "date": AnalystConsensusService._pick(row, "date", "timestamp", "updated_at"),
            "firm": AnalystConsensusService._pick(row, "firm", "analyst_firm", "company"),
            "action": AnalystConsensusService._pick(row, "action", "type"),
            "rating_from": AnalystConsensusService._pick(row, "rating_prior", "previous_rating"),
            "rating_to": rating,
            "normalized_rating": code,
            "normalized_label": RATING_LABELS[code],
            "price_target": number(AnalystConsensusService._pick(row, "price_target", "target")),
            "price_when_posted": None,
            "title": None,
            "url": PROVIDER_URLS["twelve_data"],
        }

    @staticmethod
    def _pick(payload: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if payload.get(key) not in (None, ""):
                return payload[key]
        for value in payload.values():
            if isinstance(value, dict):
                nested = AnalystConsensusService._pick(value, *keys)
                if nested not in (None, ""):
                    return nested
        return None

    @staticmethod
    def _base_provider(
        provider: str,
        *,
        status: str = "pending",
        message: str | None = None,
        configured: bool = True,
    ) -> dict[str, Any]:
        return {
            "id": provider,
            "name": PROVIDER_LABELS[provider],
            "configured": configured,
            "status": status,
            "message": message,
            "as_of": None,
            "retrieved_at": utc_now(),
            "target": None,
            "ratings": None,
            "actions": [],
            "rating_history": [],
            "limitations": [],
            "source_url": PROVIDER_URLS[provider],
        }

    @staticmethod
    def _finish_provider(result: dict[str, Any]) -> dict[str, Any]:
        has_data = bool(result.get("target") or result.get("ratings") or result.get("actions"))
        if has_data:
            result["status"] = "ready"
            result["message"] = None
        elif result["limitations"] and all("plan actual" in item for item in result["limitations"]):
            result["status"] = "restricted"
            result["message"] = "el plan actual no ofrece cobertura analítica"
        elif result["limitations"]:
            result["status"] = "unavailable"
            result["message"] = "la fuente no pudo consultarse"
        else:
            result["status"] = "no_coverage"
            result["message"] = "sin cobertura para este activo"
        result["actions"] = [item for item in result["actions"] if item]
        result["limitations"] = list(dict.fromkeys(result["limitations"]))
        return result

    @staticmethod
    def _validate_symbol(symbol: str) -> str:
        clean = str(symbol or "").upper().strip()
        if not re.fullmatch(r"[A-Z0-9.^:-]{1,24}", clean):
            raise ValueError("Ticker inválido para consultar consenso.")
        return clean

    def _is_fresh(self, retrieved_at: str) -> bool:
        try:
            parsed = datetime.fromisoformat(str(retrieved_at).replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
        return age.total_seconds() <= self.ttl_seconds
