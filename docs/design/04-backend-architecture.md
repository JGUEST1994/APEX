# 04. Backend architecture

## Stack

Python 3.11+ (CI runs 3.12, development 3.13), FastAPI, SQLAlchemy 2, Alembic, pydantic-settings, pandas/NumPy, httpx. Packaged as one installable package (`pip install -e .`) with an `apex` console command.

## Processes

| Process | Command | Phase | Responsibility |
|---|---|---|---|
| API | `apex serve` | 1 (read-only) | HTTP API for the dashboard and scripts |
| Ingestion worker | `apex ingest ...` now, scheduler later | 1 / 3 | Historical backfill, incremental updates, live bars |
| Research worker | Phase 2 | 2 | Document extraction, embeddings |
| Lab worker | Phase 5-6 | 5 | Backtests, walk-forward, Monte Carlo, tournaments |
| Trading engine | Phase 9 | 9 | Signals -> risk -> paper execution |
| Risk service | Phase 9 | 9 | Independent limit checks, kill switch, heartbeat |

All processes share the package and database. Workers coordinate through database tables (job rows with `SELECT ... FOR UPDATE SKIP LOCKED`) rather than a message broker. Redis or NATS can be added if latency needs it; it is not needed for bar-level strategies.

## API (current)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | DB status, trading mode, `live_trading_available: false`, whether external AI is enabled |
| GET | `/instruments` | Instrument list |
| GET | `/instruments/{symbol}/bars?timeframe&source&start&end&venue&limit` | Bars; 404 for unknown symbol; tz-aware times required |
| GET | `/ingestion-runs` | Recent data versions |
| GET | `/research/search?q=` | Keyword search |

OpenAPI docs are served at `/docs` when the API runs. See `docs/API.md`.

## Conventions

- Configuration only via `APEX_*` environment variables or `.env` (see `.env.example`).
- Logging through `apex.core.logging`; every handler redacts secrets. `APEX_LOG_JSON=true` for machine-readable logs.
- Errors: domain errors subclass `ApexError`; providers raise `ProviderError`; ingestion records failures in `ingestion_runs` instead of crashing a batch.
- Time is injected via `Clock` so staleness and session logic can be tested.
- Each module has unit tests; anything that touches the DB runs against SQLite and PostgreSQL.

## Authentication (Phase 10)

Local operator login with argon2-hashed passwords, short-lived JWT access tokens, roles `viewer`, `researcher`, `trader`, `admin`. Endpoints that change trading state require `trader` or `admin` and are written to `audit_log`. The API binds to `127.0.0.1` by default; remote access goes through a reverse proxy with TLS.
