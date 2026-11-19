# Project APEX

Standalone quantitative research and (eventually) trading platform for FX, US stocks/ETFs, crypto and CME index futures. APEX runs entirely on its own code and database; external AI is optional and off by default.

**Safety:** this release supports `research` and `paper` modes only. There is no live/real-money mode, and configuration refuses to enable one.

**Where things stand:** see [`docs/STATUS.md`](docs/STATUS.md). Design documents are in [`docs/design/`](docs/design/).

## Quick start (single machine, SQLite)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev,server]"
cp .env.example .env
apex db-upgrade
apex seed-instruments
apex ingest --provider synthetic --symbol ES --timeframe 1h --start 2025-01-06 --end 2025-01-11
apex backtest --strategy TF-SMA-001 --symbol ES --timeframe 1h --source synthetic --start 2025-01-06 --end 2025-01-11 --param fast=5 --param slow=20
apex serve            # http://127.0.0.1:8000/docs
pytest
```

Real data: put CSV files in `data/csv/<SYMBOL>_<timeframe>.csv` and use `--provider csv`, or `--provider binance` for crypto (public market data, no key).

## PostgreSQL

```bash
pip install -e ".[dev,postgres,server]"
export APEX_DATABASE_URL=postgresql+psycopg://apex:PASSWORD@localhost:5432/apex
apex db-upgrade
APEX_TEST_POSTGRES_URL=postgresql+psycopg://apex:PASSWORD@localhost:5432/apex_test pytest
```

Or `docker compose up` (see `docs/DEPLOYMENT.md`).
