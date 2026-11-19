# 10. Technology stack recommendation

| Area | Choice | Status | Notes / alternatives |
|---|---|---|---|
| Language (backend) | Python 3.11+ | In use | Rust or Numba for hot paths only if profiling shows a need |
| API | FastAPI | In use | Typed, OpenAPI out of the box |
| ORM / migrations | SQLAlchemy 2 + Alembic | In use | Portable PostgreSQL/SQLite |
| Config | pydantic-settings | In use | `APEX_*` env vars, `.env` |
| Data | pandas, NumPy | In use | Polars considered for large scans later; SciPy/statsmodels for stats in Phase 5 |
| Database | PostgreSQL 16 | In use | SQLite for single-machine installs |
| Vector search | pgvector | Optional migration ready | Fallback: keyword search (implemented) |
| HTTP client | httpx | In use | Injectable for tests |
| Backtesting | Own event-driven engine + vectorbt for sweeps | Phase 5 | Backtrader rejected as the core: unmaintained, slow, awkward metadata capture. Keep it as an optional cross-check |
| ML | scikit-learn, LightGBM, XGBoost; PyTorch only where it earns its place | Phase 7 | LightGBM is usually the best first choice on tabular features |
| Model serving | ONNX Runtime | Phase 7 | Removes training libs from the trading process |
| Scheduling | APScheduler in-process; DB job table | Phase 3 | Celery/Redis only if needed |
| Frontend | Next.js + React + TypeScript, TanStack Query, Tailwind/shadcn | Phase 10 | Tauri for a desktop window |
| Charts | TradingView Lightweight Charts | Phase 10 | Apache-2.0 licensed; attribution required |
| Testing | pytest; ruff for lint | In use | Hypothesis for property tests on the backtester later |
| CI | GitHub Actions (lint + tests on PostgreSQL) | In repo | |
| Packaging/deploy | Docker + Compose; systemd for bare metal | Files in repo, not yet verified | |
| Local LLM (optional) | Ollama / llama.cpp | Phase 2+ | Only via `apex.ai` |
| Embeddings | sentence-transformers (local, 384-dim) | Phase 2 | No remote dependency |

Deviations from the spec's suggested stack: Backtrader is not the core engine (reasons above). Everything else matches the spec.
