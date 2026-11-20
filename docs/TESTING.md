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
| Data infrastructure | session dates, CME/FX holidays, resampling (incl. partial buckets, daily sessions), reconciliation, incremental updates, resampled provider through the pipeline, Alpaca, Polygon and Kraken pagination/normalisation/secret-safe errors (mocked), CLI update/resample/reconcile |
| Database | migrations create schema, full downgrade/upgrade, instrument round-trip, idempotent ingestion with stable content hash, quality issues recorded, failures recorded, batching above SQLite parameter limits |
| Strategies | lifecycle transitions, no look-ahead for every registered strategy, parameter validation |
| Research | extraction (txt/md/html/pdf/csv/vtt/srt), concept/formula/rule extraction, embeddings, hybrid search, dedupe, licence note enforcement, API |
| Backtesting | hand-calculated fills and P&L, short/flip with costs, latency, futures multiplier and lot rounding, cost stress, determinism, metric helpers, experiment recording and reproducibility, CLI + API |
| Validation | PSR/DSR properties, expected max Sharpe, PBO on noise vs real edge, Monte Carlo reproducibility, walk-forward fold structure and determinism, sensitivity, hold-out, CLI |
| Tournament | promotion stops at validation, noise fails strict gates, family trial counting, family-wide DSR re-deflation, robustness score ignores raw return, human-only approval, audit trail, leaderboard filters, CLI + API |
| Regimes | HMM recovers known volatility regimes out of sample, filtered probabilities causal, rule regimes causal, stability metrics, regime-filtered strategy wrapper, CLI |
| ML | triple-barrier labels, purged/embargoed folds, logistic fit + JSON round trip, causal features, walk-forward trains only on finished labels, chance-level on noise, recovers a planted signal |
| Risk & paper | limits only tighten, every pre-trade check, reducing orders allowed, kill switch trips and needs a human reset, order state machine, idempotent paper broker, P&L and loss streaks, paper fills at next open, flatten after kill switch, risk package independence, correlation grouping (incl. inverse instruments, short history), correlated-exposure cap refuses new risk but allows reducing, reversals close the old side even when the new entry is refused, loss streak counts round trips not trims, a losing streak trips the kill switch and a human reset clears it, multi-instrument accounts share one correlated-exposure budget, single and multi paths agree, multi-instrument restart/reconcile/compare (weekend bars carry crypto only); multi-instrument strategies in paper (`tests/test_paper_multi_strategy.py`): pair positions follow the strategy's weights (>95% of bars), legs stay inside the notional cap, the kill switch flattens every leg, wrong leg counts are refused (trader and `create_account`), a bar where one leg's market is closed trades nothing, a cross-asset account restarted mid-run gives identical fills |
| Paper persistence | restart mid-run gives identical fills, no duplicate orders on replay, tampered positions halt the account until human reset, kill switch survives restart, paper commands refuse research mode, paper API endpoints are read-only, paper matches backtest direction on 100% of bars across a restart, divergence is flagged and explained |
| Meta-labelling | new-trade events, causal, only removes trades, learns to skip the losing side of a no-skill primary and improves Sharpe, warm-up follow/skip, registered ML-META-001 wraps Donchian |
| Pairs / portfolio | multi strategies registered separately, weights causal and always hedged, profits on planted cointegrated pairs but not unrelated ones, one-leg portfolio equals the single engine (equity, costs, per-trade P&L), next-open fills on aligned legs, `apex backtest-pair`, walk-forward/PBO on pairs keep the planted edge out of sample, tournament routes A/B pairs only to multi strategies, leaderboard asset class, `apex validate` on a pair |
| Cross-asset trend (H05) | `tests/test_cross_asset.py`: any number of legs (1 leg and 3-leg pairs refused), weights causal, change only on rebalance bars, inverse-volatility sizing (1:2:4 vol gives ~4:2:1 weight), long-only never shorts, profits on planted trends up and down but not on driftless walks, 3-leg walk-forward keeps the edge out of sample, `apex backtest-pair` with three symbols |
| Cross-sectional momentum (H07) | `tests/test_xs_momentum.py`: causal, flat until ranks exist, longs are the true top names at a rebalance, dollar neutral with equal weights, changes only on rebalance bars, long-only mode, bad skip/lookback refused, profits when winners persist but not on noise |
| ETF vs basket (H41) | `tests/test_etf_basket.py`: needs the ETF plus 2 or more holdings, weights causal, always hedged, equal across holdings, profits when the ETF tracks its basket but not when unrelated |
| Gradient-boosted trees (H48) | `tests/test_gbt.py`: learns an XOR-style interaction the logistic model cannot (>80% vs <60% out of sample), JSON round trip, deterministic, walk-forward with `model="gbt"` finds a planted signal, unknown model refused, ML-GBT-001 never trades against the crossover and filters some of it |
| Strategy batch 3 | `tests/test_strategies_batch3.py`: H08 holds the first-half-hour sign only over the last half hour; H10 only over the weekend, entered Friday 20:00 UTC; H44 matches the trend or fade strategy by vol regime; H49 flat before its model, then momentum when calm and RSI(2) when turbulent; H52 shorts a rejected first touch of 1.1000, exits on the 5-bar timer, ignores a second touch. Plus the registry-wide look-ahead and trade checks |
| Strategy batch 4 | `tests/test_strategies_batch4.py`: swing points are confirmed only k bars later; H15 fades a wide Asia rally only between 07:00 and 10:00 UTC; H32 fades one sweep of the Asia high per day and is flat at 16:00; H34 shorts a raid on equal highs; H36 shorts the first break of an uptrend's swing low; H46 never trades inside the opening range and is flat on the last bar; H47 buys a bullish engulfing day at last week's low; H51 buys the first pullback in a 1-minute run. Plus the registry-wide look-ahead and trade checks |
| Strategy batch 5 | `tests/test_strategies_batch5.py`: the bar-approximated volume profile finds the busiest price and a 70% value area (and NaN with no volume); the prior-session profile is NaN in the first session and equals the previous session's profile after; H09 buys near the rolling high and exits 10% below the peak close; H25 shorts after two closes back inside the value area and exits at the far edge; H26 entries are always on the far side of the prior POC and flat on the session's last bar; high-volume nodes are the busy prices (none for a flat block); H27 buys a long-wick rejection of the prior node and holds until the close, and skips it when the wick is too small. Plus the registry-wide look-ahead and trade checks |
| Session close | `tests/test_session_close.py` and `tests/test_calendars.py`: bars left in session across DST, weekends, NYSE early closes and CME's 17:00 ET close; NYSE early-close dates checked against the published 2021, 2024-2026 schedules; daily bars untouched; the five session-flat strategies hold nothing over the last bar or overnight in a backtest, differ from the old rule only at the close, and the old rule really did hold into the close |
| Scheduler | job file validation, data updates run before paper runs, re-runs add no duplicate bars or orders, failures isolated and audited, paper jobs refuse research mode, sleep-until-next-job loop, `apex schedule --once` |
| Architecture | core packages never import `apex.ai` |
| AI | disabled by default and fails safely |
| API | health, instruments, bars, 404, ingestion runs |

CI (`.github/workflows/ci.yml`) runs lint and the full suite against PostgreSQL 16 on every push and pull request, plus `pip-audit` over the runtime dependencies (run it locally with `pip-audit .`).
