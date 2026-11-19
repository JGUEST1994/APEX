# Testing

```bash
ruff check src tests
pytest                                                    # SQLite only
APEX_TEST_POSTGRES_URL=postgresql+psycopg://apex:apex@localhost/apex_test pytest   # + PostgreSQL
```

The PostgreSQL test database is **wiped** (schema dropped) by each DB test, so point it at a dedicated database.

## What is covered

| Area | Tests |
|---|---|
| Config & safety | live mode rejected; secrets hidden from repr; log redaction |
| Calendars | NYSE holidays and observed rules, DST boundaries, FX/CME/crypto sessions, expected bar generation |
| Market data | normalisation, synthetic determinism, CSV provider, Binance pagination and errors (mocked HTTP), validation of NaN / non-positive / OHLC / duplicates / conflicts / misalignment / gaps / spikes, staleness |
| Data infrastructure | session dates, CME/FX holidays, resampling (incl. partial buckets, daily sessions), reconciliation, incremental updates, resampled provider through the pipeline, Alpaca pagination/normalisation/secret-safe errors (mocked), CLI update/resample/reconcile |
| Database | migrations create schema, full downgrade/upgrade, instrument round-trip, idempotent ingestion with stable content hash, quality issues recorded, failures recorded, batching above SQLite parameter limits |
| Strategies | lifecycle transitions, no look-ahead for every registered strategy, parameter validation |
| Research | extraction (txt/md/html/pdf/csv/vtt/srt), concept/formula/rule extraction, embeddings, hybrid search, dedupe, licence note enforcement, API |
| Backtesting | hand-calculated fills and P&L, short/flip with costs, latency, futures multiplier and lot rounding, cost stress, determinism, metric helpers, experiment recording and reproducibility, CLI + API |
| Validation | PSR/DSR properties, expected max Sharpe, PBO on noise vs real edge, Monte Carlo reproducibility, walk-forward fold structure and determinism, sensitivity, hold-out, CLI |
| Tournament | promotion stops at validation, noise fails strict gates, family trial counting, family-wide DSR re-deflation, robustness score ignores raw return, human-only approval, audit trail, leaderboard filters, CLI + API |
| Architecture | core packages never import `apex.ai` |
| AI | disabled by default and fails safely |
| API | health, instruments, bars, 404, ingestion runs |

CI (`.github/workflows/ci.yml`) runs lint and the full suite against PostgreSQL 16 on every push and pull request.
