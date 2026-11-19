# 14. Project directory structure

Current layout (Phase 1). Items marked *(planned)* do not exist yet.

```
apex/
├── README.md                     quick start
├── pyproject.toml                package, dependencies, pytest + ruff config
├── .env.example                  configuration template (no secrets)
├── Dockerfile, docker-compose.yml
├── .github/workflows/ci.yml      lint + tests on PostgreSQL
├── docs/
│   ├── STATUS.md                 current state, what is tested, next steps  ← start here
│   ├── API.md, TESTING.md, DEPLOYMENT.md
│   ├── RESEARCH_JOURNAL.md, STRATEGY_HISTORY.md
│   └── design/01..14-*.md        Section 21 deliverables
├── src/apex/
│   ├── core/                     config, logging (redaction), clock, errors
│   ├── domain/                   market types (Bar, Timeframe, InstrumentSpec, CostModel), universe
│   ├── db/                       models, UTC type, sessions, migration runner
│   ├── migrations/               Alembic env + versions (0001 schema, 0002 optional pgvector)
│   ├── marketdata/
│   │   ├── base.py               MarketDataProvider + normalisation
│   │   ├── calendars.py          FX 24x5, CME Globex, NYSE RTH + holidays, crypto 24x7
│   │   ├── validation.py         quality checks + staleness
│   │   ├── repository.py         instrument/bar persistence, content hashing
│   │   ├── ingest.py             fetch → validate → store → record
│   │   └── providers/            synthetic, csv_file, binance
│   ├── research/library.py       documents, chunks, keyword search
│   ├── strategies/               interface, lifecycle, registry, reference strategy
│   ├── ai/provider.py            optional LLM interface (default: disabled)
│   ├── api/app.py                FastAPI app
│   ├── cli.py                    `apex` command
│   ├── backtest/                 (planned, Phase 5)
│   ├── ml/                       (planned, Phase 7)
│   ├── risk/                     (planned, Phase 9)
│   └── execution/                (planned, Phase 9/11)
├── tests/                        pytest suite (SQLite always, PostgreSQL when configured)
└── frontend/                     (planned, Phase 10: Next.js app)
```
