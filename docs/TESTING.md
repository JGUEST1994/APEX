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
| Database | migrations create schema, full downgrade/upgrade, instrument round-trip, idempotent ingestion with stable content hash, quality issues recorded, failures recorded, batching above SQLite parameter limits |
| Strategies | lifecycle transitions, no look-ahead for every registered strategy, parameter validation |
| Research | chunking, dedupe by hash, keyword search |
| AI | disabled by default and fails safely |
| API | health, instruments, bars, 404, ingestion runs |

CI (`.github/workflows/ci.yml`) runs lint and the full suite against PostgreSQL 16 on every push and pull request.
