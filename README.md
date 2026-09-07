# Portfolio Ledger Agent

A local-first investment tracker that combines a conversational ledger, portfolio valuation, and source-backed AI research.

I built this side project to bring spreadsheets, market data, and research notes into one place—with every transaction reviewed before it is saved.

**Python · Vanilla JavaScript · SQLite · OpenAI Responses API · Docker**

## What it does

- **Portfolio:** holdings, cash, daily performance, and analyst consensus.
- **Transactions:** describe a trade in Spanish, review the draft, confirm it, and export to Excel.
- **Research:** investigate assets or your portfolio with cited sources and saved reports.
- **Settings:** default account, currency, and service status.

The responsive interface works on mobile and desktop. Records stay local; optional AI and market-data features use external APIs. The UI is currently in Spanish.

## Run locally

With Docker installed, copy `.env.example` to `.env`, add the API keys for the services you want to use, then run:

```bash
docker compose up --build -d
```

Open [localhost:8765](http://127.0.0.1:8765).

## Documentation

- [Getting started](docs/getting-started.md) — setup, configuration, authentication, and deployment.
- [Features and usage](docs/features.md) — transactions, research, valuation, and analyst consensus.
- [Architecture and development](docs/architecture.md) — design decisions, data privacy, tests, and roadmap.

This app does not connect to a broker or execute trades. It provides tracking and research support, not financial advice.
