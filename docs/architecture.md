# Architecture and development

[Back to the project](../README.md)

## Engineering decisions

- **Review before persistence.** Natural-language input becomes a draft. Deterministic validation and explicit confirmation sit between extraction and storage.
- **SQLite as the source of truth.** Confirmed activity is recorded locally, with an audit trail and an Excel copy for convenient review.
- **Evidence stays separate from interpretation.** Quotes and analyst consensus retain their sources; research distinguishes facts, outside opinions, and the agent's own reading.
- **A small frontend stack.** HTML, CSS, and vanilla JavaScript keep the interface straightforward, with accessible tab navigation and responsive layouts.
- **Local-first, with explicit external calls.** Records live locally. AI and market-data features use configured external services; see [Research and opportunities](features.md#research-and-opportunities) for the data flow.

## Why SQLite

SQLite is a good fit for a local, single-user agent: it is transactional, portable, backup-friendly, and requires no separate database service. Docker does not make SQLite durable by itself; the mounted `data/` directory does.

If the application later runs on multiple machines, uses multiple write workers, or becomes a shared hosted service, migrate the persistence layer to PostgreSQL. The included login is intentionally single-user; it is not a multi-tenant identity system.

## Project structure

```text
app.py                 HTTP server and API routes
src/investor_agent/    Parsing, ledger, research, market data, and authentication
static/                Responsive HTML, CSS, and JavaScript interface
assets/                Demonstration Excel template
tests/                 Automated tests with temporary data
scripts/               Optional live provider diagnostics
data/                  Local runtime records (excluded from Git)
```

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

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The unit test suite uses temporary databases and workbooks and does not call OpenAI or market-data APIs. The provider diagnostic is an explicit live-network check and may consume free-tier request quotas.

## Roadmap

- Portfolio goals, entry and exit plans, and risk limits.
- Personal entry and exit plans with timestamps, assumptions, and user decisions.
- Watchlists, alerts, and explicit follow-up signals for each researched asset.
- Foreign-exchange conversion and explicit exchange/listing selection for international assets.

This project provides organizational and research support, not professional financial advice.
