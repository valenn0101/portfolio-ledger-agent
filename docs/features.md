# Features and usage

[Back to the project](../README.md)

## Explore the app

The responsive interface is organized around four tabs:

| Tab | Purpose |
| --- | --- |
| **Portfolio — Cartera** | View holdings, available cash, market value, daily changes, unrealized returns, and analyst consensus. |
| **Transactions — Movimientos** | Describe transactions, review and confirm drafts, inspect recent activity, and download the Excel ledger. |
| **Research — Investigación** | Investigate an asset, review the portfolio, explore candidates, and revisit saved reports with cited sources. |
| **Settings — Configuración** | Set a default account and currency, and check the status of local storage, Excel, and AI. |

Navigation stays visible while scrolling. Holdings use a card layout on small screens, and switching tabs preserves form inputs and transaction drafts. Research can finish while another tab is open, with a notification when the result is available. Each holding also offers a shortcut to prepare an asset-specific research query.

The UI is currently in Spanish, reflecting the project's original personal use case; this documentation is in English.

## What it includes

- Conversational registration of purchases, sales, deposits, withdrawals, dividends, fees, and taxes.
- Strict JSON extraction with GPT-5.6 Terra through the OpenAI Responses API.
- Explicit confirmation before persistence.
- SQLite as the source of truth and an Excel workbook for convenient review or import into Google Sheets.
- Portfolio quantities, cash balances, recent activity, and an audit trail.
- Automatic cash updates after confirmed purchases, sales, deposits, withdrawals, dividends, fees, and taxes when a compatible balance exists.
- Audited amendments that reverse the previous cash effect before applying the corrected movement, preventing duplicate debits or credits.
- Live portfolio valuation with current price, previous-close movement, weighted-average cost, unrealized gain/loss, and cash-inclusive total.
- Normalized quote fallback through Twelve Data, Finnhub, Tiingo, and Financial Modeling Prep, with a persistent SQLite cache.
- A portfolio consensus column with normalized Buy/Strong Buy/Hold/Reduce/Sell labels, median or average analyst target, implied upside/downside, source coverage, and freshness.
- A responsive analyst modal with low/median/average/high targets, recommendation distribution, provider-by-provider comparison, plan limitations, and paginated individual rating history.
- Structured adapters for FRED, U.S. Treasury, BLS, and SEC EDGAR.
- Current web research for an asset, the whole portfolio, a free-form question, or up to five opportunities to investigate.
- Reports that separate verifiable facts, external consensus, the agent's interpretation, risks, and next steps.
- Clickable inline citations plus a local SQLite history of every research report.
- Visible running, completed, incomplete, and error states with elapsed time.
- An optional orientative opinion labelled Buy, Hold, Reduce, Sell, or Inconclusive, plus dated entry/hold/reduce-or-sell price zones when the evidence is sufficient.
- A local parser fallback when OpenAI is unavailable.
- Optional single-user login with signed, expiring, HTTP-only session cookies.
- Responsive layouts for the chat, portfolio cards, research reports, and login screen.
- Docker support with persistent local data.

## Conversational registration

Movement messages are interpreted by GPT first and checked with deterministic parsing rules before a draft is shown. Flexible Spanish phrasing and decimal commas are supported, for example:

```text
Agregar a mi cartera 0,902 acciones de GOOGL compradas el 29 de julio a 332,98 por accion
```

This produces a reviewable purchase draft; it is not persisted until the user confirms it. Portfolio questions such as `resumen de cartera` and research questions such as `¿qué comprar para mi cartera?` remain separate intents.

While a draft is pending, any field can be corrected conversationally without repeating the movement, for example `el precio correcto es 330,50`, `cambiá el ticker a GOOG`, or `en realidad fue una venta`. Confirmed movements remain immutable in the audit trail.

## Research and opportunities

Use the **Investigación** area in the web interface, or ask directly in the conversation, for example:

- `Analizá Visa (V): resultados recientes, consenso y riesgos.`
- `Buscá hasta cinco acciones de Estados Unidos que merezcan investigación.`
- `Revisá mi cartera actual y decime qué riesgos debería vigilar.`

Research uses the OpenAI Responses API with web search. The app sends the question and a compact summary of current positions and cash; it does not send credentials or the complete transaction history. Reports and source metadata are saved in the local SQLite database. A web search may generate OpenAI API usage charges.

The current version deliberately avoids calculating a position size or presenting an opinion as an instruction. When the optional opinion is requested, it also asks for price zones with currency, date, horizon, method, confidence, and invalidation conditions. A zone requires a dated market price, primary fundamentals, and an explainable valuation method; otherwise the report must say that it is not calculable with sufficient data. Until goals and risk limits are configured, the result states its assumptions and does not calculate a position size.

The research prompt prioritizes free sources by purpose: SEC EDGAR and company investor-relations pages for primary fundamentals; Nasdaq pages for market cross-checks; U.S. Treasury, BLS, and FRED for macro assumptions; and Yahoo Finance or Investing.com only as secondary checks. Runtime adapters are available for Twelve Data, Financial Modeling Prep, FRED, Finnhub, Tiingo, SEC EDGAR, U.S. Treasury, and BLS. Their connection state is visible in the source catalog.

```bash
python3 scripts/check_market_providers.py --pretty
python3 scripts/check_analyst_sources.py
```

The first command checks credentials, quote coverage, freshness, cross-provider consistency, a non-U.S. symbol, SEC EDGAR, U.S. Treasury, and BLS. The second reports which analyst datasets the current FMP, Twelve Data, and Finnhub plans allow, along with response shapes. Neither command prints API keys. For SEC automated access, set `SEC_USER_AGENT` to an application name plus a real contact email, following SEC fair-access guidance.

## Portfolio valuation

The **Tus inversiones hoy** section requests normalized quotes in this order:

1. Twelve Data
2. Finnhub
3. Tiingo
4. Financial Modeling Prep

Quotes are cached for 15 minutes and persisted in the `market_quotes` SQLite table. The refresh button bypasses the cache. The interface displays:

- current price and market timestamp;
- daily change per position versus the previous close, in USD and percent;
- weighted-average acquisition cost after partial sales;
- unrealized gain/loss, market value, allocation, cash, and total portfolio value.

USD and USDC are compared at explicit 1:1 parity. Other currency combinations remain unvalued until a foreign-exchange adapter is added.

Structured endpoints are also available at `/api/market/status`, `/api/market/macro`, and `/api/market/sec?symbol=V`. SEC stays marked as not configured until `SEC_USER_AGENT` contains a real contact email.

## Analyst consensus

Consensus loads independently after portfolio prices so an analyst API outage cannot block portfolio valuation. Summary cells show a normalized external label, the median target when available (otherwise the provider average), implied upside or downside versus the current quote, opinion count, and primary source. `Sin cobertura` is a distinct state and is never converted into `Mantener`.

The detail modal keeps provider methodologies separate instead of averaging them together. It shows:

- target low, median, average, and high values;
- Strong Buy, Buy, Hold, Sell, and Strong Sell counts;
- the exact provider chosen for the compact cell;
- every configured provider's coverage, freshness, cache state, and plan limitations;
- individual grade changes when the subscribed dataset exposes them, loaded 50 rows at a time.

FMP is preferred for target and grade consensus, Finnhub provides a second recommendation trend, and Twelve Data is used when the configured plan allows its analysis endpoints. Price-target news or analyst-level details can require a higher subscription even when aggregate consensus is available. The application preserves the original rating vocabulary in the modal and uses a documented normalized Spanish label only for comparison.

Analyst snapshots are stored in SQLite's `market_snapshots` table for 24 hours by default. Change `ANALYST_CONSENSUS_TTL_SECONDS` if needed. The refresh button bypasses both quote and consensus caches; opening the modal retrieves or reuses the independently cached detail.

The related JSON endpoints are:

- `/api/portfolio/consensus`
- `/api/market/consensus?symbol=V`
- `/api/market/consensus?symbol=V&details=1`

These values are external estimates, not guarantees or personalized exit prices. The app deliberately keeps the agent's optional interpretation in the Research section rather than merging it with analyst consensus.
