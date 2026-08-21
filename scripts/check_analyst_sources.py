#!/usr/bin/env python3
"""Diagnostica acceso y forma de respuestas sin imprimir credenciales."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investor_agent.config import load_env_file  # noqa: E402


load_env_file(ROOT / ".env")


def request_json(
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    target = f"{url}?{urlencode(params)}"
    try:
        with urlopen(
            Request(target, headers={"Accept": "application/json", **(headers or {})}),
            timeout=20,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return {"ok": False, "status": error.code, "shape": None}
    except (URLError, TimeoutError, ValueError) as error:
        return {"ok": False, "error": type(error).__name__, "shape": None}

    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("result") or payload
    first = rows[0] if isinstance(rows, list) and rows else rows
    keys = sorted(first.keys()) if isinstance(first, dict) else []
    count = len(rows) if isinstance(rows, list) else (1 if isinstance(rows, dict) else 0)
    api_error = None
    if isinstance(payload, dict):
        api_error = payload.get("message") if payload.get("status") == "error" else None
        api_error = api_error or payload.get("Error Message")
    return {
        "ok": not bool(api_error),
        "status": 200,
        "count": count,
        "keys": keys,
        "api_error": api_error,
    }


def main() -> None:
    symbol = "V"
    fmp = os.getenv("FMP_API_KEY", "").strip()
    twelve = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    finnhub = os.getenv("FINNHUB_API_KEY", "").strip()
    result: dict[str, Any] = {"symbol": symbol, "providers": {}}

    result["providers"]["fmp"] = {
        name: request_json(
            f"https://financialmodelingprep.com/stable/{endpoint}",
            params={"symbol": symbol, "apikey": fmp, **extra},
        )
        for name, endpoint, extra in (
            ("price_target_consensus", "price-target-consensus", {}),
            ("grades_consensus", "grades-consensus", {}),
            ("price_target_news", "price-target-news", {"page": 0, "limit": 10}),
            ("grades", "grades", {}),
        )
    } if fmp else {"configured": False}

    result["providers"]["twelve_data"] = {
        name: request_json(
            f"https://api.twelvedata.com/{endpoint}",
            params={"symbol": symbol, "apikey": twelve},
        )
        for name, endpoint in (
            ("price_target", "price_target"),
            ("recommendations", "recommendations"),
            ("analyst_ratings", "analyst_ratings/light"),
        )
    } if twelve else {"configured": False}

    result["providers"]["finnhub"] = {
        name: request_json(
            f"https://finnhub.io/api/v1/{endpoint}",
            params={"symbol": symbol, "token": finnhub},
        )
        for name, endpoint in (
            ("recommendations", "stock/recommendation"),
            ("price_target", "stock/price-target"),
        )
    } if finnhub else {"configured": False}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
