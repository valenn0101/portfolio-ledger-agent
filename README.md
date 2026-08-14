# Portfolio Ledger Agent

A local-first investment ledger operated through a conversational Spanish interface. It turns natural-language messages into reviewable drafts, reports missing fields, and requires explicit confirmation before writing anything.

> Early MVP: it records and audits portfolio activity. It does not connect to a broker or place orders.

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

The web interface is currently in Spanish because the first use case is a Spanish-speaking personal portfolio.

## Conversational registration

Movement messages are interpreted by GPT first and checked with deterministic parsing rules before a draft is shown. Flexible Spanish phrasing and decimal commas are supported, for example:

```text
Agregar a mi cartera 0,902 acciones de GOOGL compradas el 29 de julio a 332,98 por accion
```

This produces a reviewable purchase draft; it is not persisted until the user confirms it. Portfolio questions such as `resumen de cartera` and research questions such as `¿qué comprar para mi cartera?` remain separate intents.

While a draft is pending, any field can be corrected conversationally without repeating the movement, for example `el precio correcto es 330,50`, `cambiá el ticker a GOOG`, or `en realidad fue una venta`. Confirmed movements remain immutable in the audit trail.

## Run with Docker (recommended)

Requirements: Docker Engine or Docker Desktop with Compose.

```bash
cp .env.example .env
```

Add your OpenAI Platform key to `.env`:

```dotenv
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-5.6-terra
```

Then start the application:

```bash
docker compose up --build -d
```

Open <http://127.0.0.1:8765>. To stop it:

```bash
docker compose down
```

`compose.yaml` bind-mounts `./data` into the container. Rebuilding or replacing the container therefore does not delete the SQLite database or generated workbook.

The server reads the `PORT` environment variable when a hosting provider assigns one. Local Docker Compose continues to use port `8765` by default.

On Linux/WSL, Compose defaults to UID/GID `1000:1000` so SQLite can write to the bind mount without running the container as root. If your account uses different values, set `LOCAL_UID` and `LOCAL_GID` in `.env` using the output of `id -u` and `id -g`.

## Private access

Authentication remains optional for local development. Enable it for every deployment reachable from the internet:

```dotenv
APP_REQUIRE_AUTH=1
APP_USERNAME=your_username
APP_PASSWORD=a-long-unique-password
APP_SESSION_SECRET=a-random-secret-with-at-least-32-characters
APP_SESSION_HOURS=12
APP_SECURE_COOKIES=auto
```

Generate a session secret without reusing the login password:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

The server refuses to start when authentication is enabled with missing or weak values. Passwords and session secrets are read only from the environment. The browser receives a signed, expiring, HTTP-only cookie; API credentials and the login password are never stored in browser storage. Repeated failed logins are temporarily throttled. `/api/health` remains public so a hosting platform can monitor the service, while the interface, Excel download, and all portfolio APIs require a valid session.

## Deploy on Railway

The repository includes `railway.toml` and a Docker health check. A straightforward private single-user deployment is:

1. Create a Railway project from the GitHub repository and let it detect the `Dockerfile`.
2. Add a persistent volume mounted at `/app/data`; this keeps SQLite and the generated workbook across redeploys.
3. Add the OpenAI and market-data variables plus all `APP_*` authentication variables shown above. Use `APP_SECURE_COOKIES=always` for the Railway HTTPS domain.
4. Set `RAILWAY_RUN_UID=0`. Railway volumes are mounted as root, so this runtime override lets the existing image write to `/app/data`.
5. Generate a public domain only after the health check is green and authentication has been tested.

Railway supplies `PORT` automatically. Do not add the local `data/` directory or `.env` to the service image. See Railway's official [volume guide](https://docs.railway.com/volumes) and [Dockerfile deployment guide](https://docs.railway.com/guides/dockerfiles) for the current platform steps and limits.

## Run without Docker

Python 3.12 or newer is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./iniciar_con_ia.sh
```

The application loads `.env` automatically and opens <http://127.0.0.1:8765>.

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
```

The command checks credentials, quote coverage, freshness, cross-provider consistency, a non-U.S. symbol, SEC EDGAR, U.S. Treasury, and BLS. It never prints API keys. For SEC automated access, set `SEC_USER_AGENT` to an application name plus a real contact email, following SEC fair-access guidance.

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

## Data and privacy

Runtime data is written to:

- `data/inversiones.db`: SQLite database and audit source of truth.
- `data/movimientos.xlsx`: generated spreadsheet copy.
- `assets/plantilla_base.xlsx`: public demonstration template used to initialize the workbook.

The following are intentionally excluded from Git and the Docker image:

- `.env` and API credentials.
- Everything generated inside `data/`, except its empty placeholder.
- Personal portfolio import and verification scripts.
- Local backups, virtual environments, and caches.

Before making a fork public, run `git status --ignored` and confirm that no personal exports, screenshots, databases, or credentials were added under another path.

## Why SQLite

SQLite is a good fit for a local, single-user agent: it is transactional, portable, backup-friendly, and requires no separate database service. Docker does not make SQLite durable by itself; the mounted `data/` directory does.

If the application later runs on multiple machines, uses multiple write workers, or becomes a shared hosted service, migrate the persistence layer to PostgreSQL. The included login is intentionally single-user; it is not a multi-tenant identity system.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The unit test suite uses temporary databases and workbooks and does not call OpenAI or market-data APIs. The provider diagnostic is an explicit live-network check and may consume free-tier request quotas.

## Roadmap

- Portfolio goals, entry and exit plans, and risk limits.
- Recommendation history with timestamps, sources, confidence, and user decisions.
- Watchlists, alerts, and explicit follow-up signals for each researched asset.
- Foreign-exchange conversion and explicit exchange/listing selection for international assets.

This project provides organizational and research support, not professional financial advice.
