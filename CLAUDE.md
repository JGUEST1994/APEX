# Notes for AI coding assistants (and humans)

- Read `docs/STATUS.md` first; update it at the end of every working session.
- Never add a live/real-money trading path. Trading modes are `research` and `paper` only.
- Core modules (`core`, `domain`, `db`, `marketdata`, `strategies`, future `backtest`, `risk`, `execution`) must not import `apex.ai`.
- Run `ruff check src tests` and `pytest` before committing. Set `APEX_TEST_POSTGRES_URL` to also test on PostgreSQL. Never claim tests pass without running them.
- Schema changes go through a new Alembic migration in `src/apex/migrations/versions/` (do not edit applied migrations).
- Timestamps are timezone-aware UTC everywhere; bar `ts` is the bar open time.
