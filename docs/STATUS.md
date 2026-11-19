# APEX project status

Last updated: 2026-11-19. Current phase: **Phases 1, 2, 3 (core) and 5 (core) complete; milestone 1 reached → next: Phase 4 strategy library, then Phase 5 validation.**
Update this file at the end of every working session so work can continue from any tool.

## Milestone 1 target (spec Section 21)

"A working standalone application capable of collecting market data, storing research, developing strategies and running historical backtests."

| Capability | State |
|---|---|
| Collecting market data | ✅ Historical ingestion with validation, idempotent storage and data versioning. Providers: synthetic, CSV, Binance (mock-tested only) |
| Storing research | ✅ Import of txt/md/html/pdf/csv/vtt/srt, concept/formula/rule extraction, local embeddings, hybrid BM25 + vector search |
| Developing strategies | 🟡 Interface, lifecycle, plugin registry, one reference strategy. Hypothesis library is Phase 4 |
| Running historical backtests | ✅ Event-driven backtester with costs, latency, metrics; every run recorded as a reproducible experiment. Walk-forward / Monte Carlo / DSR still to come |

## Implemented and tested

Tests last run: 2026-11-19, `ruff check` clean, **113 passed**, with database tests run on both SQLite and PostgreSQL 16 (`APEX_TEST_POSTGRES_URL` set).

- `apex.core`: env config, live trading impossible to enable, secret redaction in logs, injectable clock.
- `apex.domain`: Bar, Timeframe, InstrumentSpec, CostModel; 22-instrument default universe (FX majors, US ETFs/stocks, BTC/ETH, ES/MES/NQ/MNQ).
- `apex.db`: 12 tables, Alembic migrations `0001` (schema) and `0002` (pgvector, no-op when unavailable), tested upgrade/downgrade on both engines.
- `apex.marketdata`: provider interface + normalisation; calendars (FX 24x5, CME Globex with daily halt, NYSE RTH with computed holidays, crypto 24x7); validation (NaN, non-positive, OHLC consistency, duplicates/conflicts, misalignment, out-of-session, gaps, spikes) and staleness check; repository with content hashing; ingestion pipeline that records runs and issues.
- `apex.research` (Phase 2): file extraction (`extract.py`: text/markdown, HTML with metadata, PDF text layer + metadata via pypdf, CSV dataset summaries, VTT/SRT transcripts), deterministic extraction of concepts, risk techniques, formulas and if/then trading rules stored as untested hypotheses (`concepts.py`), local embeddings (`embeddings.py`: dependency-free hashing embedder by default, sentence-transformers optional), hybrid BM25 + cosine search, concept and formula lookup, licence note required on import (`library.py`). Migration `0003` adds portable chunk vectors.
- Phase 3 data infrastructure: calendar `session_date` (FX/CME evening sessions roll to the next trading day), approximate CME/FX holiday closures; `resample.py` (calendar-aware resampling with partial-bucket detection); `ResampledProvider` so derived timeframes go through the normal validation + versioning pipeline (source `<src>:rs<tf>`); `reconcile.py` cross-source comparison; `update_bars` incremental, cron-safe updates of completed bars only; `AlpacaStocksProvider` for US equities (mock-tested; needs `APEX_ALPACA_KEY_ID`/`APEX_ALPACA_SECRET_KEY`). CLI: `update`, `resample`, `reconcile`; `ingest` gains `--provider alpaca`.
- `apex.backtest` (Phase 5 core): bar-based event-driven engine (`engine.py`) that fills at the next bar's open (configurable latency, never same-bar), sizes by equity x leverage rounded to lot size, trades only on target changes, charges commission/spread/slippage/funding from the instrument `CostModel` with a stress multiplier; metrics (`metrics.py`): total return, CAGR, volatility, Sharpe, Sortino, max drawdown, Calmar, win rate, profit factor, expectancy, average win/loss, max consecutive losses, trades/year, exposure, % positive months, costs; reproducible experiments (`experiments.py`) recording data SHA-256, git commit, full params and config, and registering strategy versions with a code hash (refuses to reuse a version number after the strategy code changes). CLI `apex backtest`, `apex strategies`; API `/experiments` (all flagged simulated). Hand-calculated reference tests for fills, P&L, costs, flips, futures multipliers.
- Architecture test: core packages may not import `apex.ai` (AST scan in `tests/test_architecture.py`).
- `apex.strategies`: interface, 7-state lifecycle with transition rules, entry-point plugin registry, generic no-look-ahead test.
- `apex.ai`: optional provider interface, disabled by default.
- `apex.api`: read-only FastAPI (health, instruments, bars, ingestion runs, research search, research documents).
- `apex` CLI: db-upgrade, seed-instruments, ingest, research-import (file or folder), research-search, serve (CLI smoke-tested end to end on SQLite).
- CI workflow for GitHub Actions (lint + tests on PostgreSQL).

## Written but not verified

- `Dockerfile` and `docker-compose.yml` (no Docker daemon in the build environment).
- `BinanceSpotProvider` and `AlpacaStocksProvider` against the real endpoints (sandbox network blocks them; logic tested with mocked HTTP).

## Known limitations

- NYSE early closes treated as full days; CME/FX holidays approximated (25 Dec, 1 Jan, CME Good Friday); other exchange holidays and early closes will show as gap warnings.
- Research search scans all chunks in memory: fine for tens of thousands of chunks; move to PostgreSQL full-text + pgvector beyond that.
- The default embedder is lexical (feature hashing). Install `sentence-transformers` with a local model for true semantic similarity.
- Scanned PDFs (no text layer) are rejected: OCR not supported yet.
- YouTube: APEX imports transcript files the operator obtained through an authorised route; it does not fetch from YouTube. An official-API fetcher (YouTube Data API captions for channels you own, or platform exports) is a later addition.
- No authentication on the API yet: keep it on localhost.
- Backtester does not yet model: margin calls, partial fills, intrabar stop/limit orders, market impact beyond bps slippage, futures roll. Positions open at the end are marked at the last close and counted as a trade (`closed_at_end`).
- Reported Sharpe etc. are raw; multiple-testing adjustment (Deflated Sharpe, PBO) arrives with Phase 5 validation.
- Daily bars use 00:00 UTC of the session date as `ts`; providers with other conventions must be mapped.

## Next steps (in order)

1. Phase 4: write the 50 strategy hypotheses as specs; implement the first ~10.
2. Phase 5 remainder: walk-forward, Monte Carlo, sensitivity, DSR/PBO.
3. Phase 3 remainder: Databento (futures), Polygon/Massive, Dukascopy (FX), Kraken adapters; in-process scheduler.
4. pip-audit in CI.

## Decisions needed from Jake

- Which paid data sources, if any, to start with (see design 08). Free sources are enough to begin Phase 3.
- Confirm broker preference for later paper trading (recommendation: Interactive Brokers).
