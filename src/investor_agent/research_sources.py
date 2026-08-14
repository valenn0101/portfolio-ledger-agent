from __future__ import annotations

from typing import Any


FREE_RESEARCH_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "name": "SEC EDGAR",
        "category": "Fundamentales y presentaciones",
        "access": "Sin clave",
        "integration": "runtime_api",
        "provider_id": "sec_edgar",
        "url": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
        "domains": ["sec.gov", "data.sec.gov"],
        "use": "10-K, 10-Q, 8-K, presentaciones y hechos XBRL de emisores de EE. UU.",
    },
    {
        "name": "Relaciones con inversores",
        "category": "Resultados y guía corporativa",
        "access": "Sin clave",
        "integration": "web",
        "url": None,
        "domains": [],
        "use": "Comunicados, presentaciones, guidance y transcripciones publicadas por cada empresa.",
    },
    {
        "name": "Nasdaq Market Activity",
        "category": "Precio y actividad de mercado",
        "access": "Web gratuita; puede tener demora",
        "integration": "web",
        "url": "https://www.nasdaq.com/market-activity",
        "domains": ["nasdaq.com"],
        "use": "Cotizaciones, históricos, volumen y calendario de mercado para contraste.",
    },
    {
        "name": "U.S. Treasury",
        "category": "Tasas de referencia",
        "access": "Sin clave",
        "integration": "runtime_api",
        "provider_id": "us_treasury",
        "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates",
        "domains": ["home.treasury.gov"],
        "use": "Curva de rendimientos y tasas libres de riesgo para supuestos de valuación.",
    },
    {
        "name": "BLS Public Data",
        "category": "Inflación y empleo",
        "access": "Sin clave con límites",
        "integration": "runtime_api",
        "provider_id": "bls",
        "url": "https://www.bls.gov/developers/home.htm",
        "domains": ["bls.gov"],
        "use": "IPC, empleo, salarios y otras series oficiales de Estados Unidos.",
    },
    {
        "name": "FRED",
        "category": "Macroeconomía",
        "access": "Cuenta y clave gratuitas para API",
        "integration": "runtime_api",
        "provider_id": "fred",
        "url": "https://fred.stlouisfed.org/docs/api/fred/overview.html",
        "domains": ["fred.stlouisfed.org", "stlouisfed.org"],
        "use": "Series macroeconómicas y sus revisiones históricas mediante FRED y ALFRED.",
    },
    {
        "name": "Alpha Vantage",
        "category": "Mercado y fundamentales",
        "access": "Clave gratuita; cupo limitado",
        "integration": "optional_api",
        "url": "https://www.alphavantage.co/premium/",
        "domains": ["alphavantage.co"],
        "use": "Cotizaciones, series temporales, indicadores y algunos datos fundamentales.",
    },
    {
        "name": "Twelve Data",
        "category": "Mercado",
        "access": "Plan Basic gratuito; cupo limitado",
        "integration": "runtime_api",
        "provider_id": "twelve_data",
        "url": "https://twelvedata.com/pricing",
        "domains": ["twelvedata.com"],
        "use": "Cotizaciones y series de acciones, ETF, forex y cripto según cobertura del plan.",
    },
    {
        "name": "Finnhub",
        "category": "Mercado, noticias y perfil",
        "access": "Plan personal gratuito; cupo limitado",
        "integration": "runtime_api",
        "provider_id": "finnhub",
        "url": "https://finnhub.io/pricing",
        "domains": ["finnhub.io"],
        "use": "Cotización, perfil de empresa y noticias; algunas métricas son solo de pago.",
    },
    {
        "name": "Tiingo",
        "category": "Cierres de mercado y fundamentales",
        "access": "Cuenta y token; plan gratuito con límites",
        "integration": "runtime_api",
        "provider_id": "tiingo",
        "url": "https://www.tiingo.com/documentation/end-of-day",
        "domains": ["tiingo.com"],
        "use": "Cierres diarios, historial y verificación cruzada de cotizaciones.",
    },
    {
        "name": "Financial Modeling Prep",
        "category": "Mercado y fundamentales normalizados",
        "access": "Clave gratuita; cobertura según plan",
        "integration": "runtime_api",
        "provider_id": "fmp",
        "url": "https://site.financialmodelingprep.com/developer/docs/quickstart",
        "domains": ["financialmodelingprep.com"],
        "use": "Perfiles y estados financieros normalizados; algunas cotizaciones requieren un plan superior.",
    },
    {
        "name": "Yahoo Finance e Investing.com",
        "category": "Contraste secundario",
        "access": "Web gratuita; datos pueden tener demora",
        "integration": "web",
        "url": "https://finance.yahoo.com/",
        "domains": ["finance.yahoo.com", "investing.com"],
        "use": "Precio visible, noticias y consenso como contraste, nunca como única base de valuación.",
    },
)


def public_source_catalog(
    market_status: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Devuelve el catálogo y el estado real de los adaptadores configurados."""

    configured = {
        item["id"]: bool(item.get("configured"))
        for group in ("providers", "official_sources")
        for item in (market_status or {}).get(group, [])
    }
    result = []
    for source in FREE_RESEARCH_SOURCES:
        item = {
            key: list(value) if isinstance(value, list) else value
            for key, value in source.items()
        }
        if source.get("integration") == "runtime_api":
            item["configured"] = configured.get(source.get("provider_id"), False)
        result.append(item)
    return result


def source_policy_prompt() -> str:
    """Resume al modelo la jerarquía de fuentes y sus limitaciones."""

    lines = []
    for source in FREE_RESEARCH_SOURCES:
        domains = ", ".join(source["domains"]) or "sitio oficial de la empresa"
        lines.append(
            f'- {source["name"]} ({domains}; {source["access"]}): {source["use"]}'
        )
    return "\n".join(lines)
