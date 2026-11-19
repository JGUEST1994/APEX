# 11. Development roadmap

Two stages, as the spec recommends: **Stage 1, the Trading Laboratory** (Phases 1–8, 10) proves whether any strategy deserves capital. **Stage 2, the Autonomous Platform** (Phases 9, 11–14) adds paper trading, then brokers. Real-money trading is a separate, explicit decision after Stage 2 paper results, not a phase that happens automatically.

Each phase ends with working, tested software and an updated `docs/STATUS.md`.

| Phase | Deliverable | Exit criteria | Status |
|---|---|---|---|
| 1. Foundations | Architecture docs; package, config, logging; schema + migrations; market data interface, calendars, validation, ingestion, versioning; research library v0; strategy interface; read-only API; CLI; CI | Tests green on SQLite and PostgreSQL | **Done** (this milestone) |
| 2. Research library | PDF/HTML/CSV/transcript extraction (pypdf, trafilatura, official YouTube Data API captions where permitted), metadata + formula/concept extraction, local embeddings, pgvector hybrid search, import-linter AI contract | Search "VWAP mean reversion" returns relevant chunks from a test corpus | Next |
| 3. Historical data | Scheduled incremental ingestion, more providers (Databento futures, Polygon/Massive or Alpaca equities, Dukascopy FX, Kraken), bar resampling, multi-source reconciliation, data health dashboard data | 5+ years of 1-min data for the core universe stored and validated | |
| 4. Strategy library | 50 documented strategy hypotheses as specs; ~10 implemented first across categories, each with look-ahead test | All implemented strategies pass interface tests | |
| 5. Backtesting engine | Event-driven engine with cost model, metrics, walk-forward, Monte Carlo, sensitivity, DSR/PBO, reproducible experiments | Re-running any experiment reproduces metrics exactly; engine agrees with a hand-calculated reference | |
| 6. Tournament | Leaderboard, promotion gates, multiple-testing accounting | Lifecycle transitions automated up to `paper_trading` only | |
| 7. ML engine | Feature store, training jobs, model registry, ONNX inference | ML strategy beats non-ML baseline out-of-sample or is dropped | |
| 8. Regime detection | HMM / clustering / volatility regimes, used as strategy filters | Regime labels stable and validated out-of-sample | |
| 9. Paper trading | Signal engine, independent risk service with kill switch, paper broker, portfolio, restart + reconciliation | 30 days continuous paper running with no unhandled errors | |
| 10. Dashboard | Next.js UI with all spec pages; auth + RBAC | Operator can run the system from the UI | |
| 11. Broker integration | IBKR (paper) then Alpaca, Kraken adapters, conformance tests | Paper orders round-trip through a real broker paper account | |
| 12. Autonomous improvement | Deterioration detection, automatic re-validation, journal | Degrading strategies suspended automatically | |
| 13. Deployment | Hardened Docker/systemd, backups, monitoring, restore drill | Restore from backup and resume safely in a drill | |
| 14. Commercialisation | Multi-tenant workspaces, licensing | Only if decided | Optional |

## Near-term task list (Phase 2 and 3)

See `docs/STATUS.md` → "Next steps" for the live list.
