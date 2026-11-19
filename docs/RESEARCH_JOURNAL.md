# Research journal

Human-readable log of research decisions. The application also keeps a `research_journal` table for machine-generated entries.

## 2026-11-19: Phase 1 foundations
- Decision: modular monolith on PostgreSQL/SQLite rather than microservices (see design 01).
- Decision: own event-driven backtester as the source of truth (Phase 5); Backtrader not used as the core.
- Decision: strategies emit target positions in [-1, 1]; the backtester fills on the next bar's open.
- Hypothesis bank not started yet; the only strategy is the reference SMA crossover used to test the interface. It is unvalidated and makes no claim of profitability.
