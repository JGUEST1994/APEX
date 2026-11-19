# 06. Trading engine architecture

Covers strategy lab, backtesting, tournament, risk and execution. Only the strategy interface and lifecycle exist today (`src/apex/strategies/`). Everything else here is the design that Phases 4 to 11 implement.

## Pipeline

```
bars ─▶ features ─▶ Strategy.target_position() ─▶ sizing ─▶ RiskService.check() ─▶ OrderManager ─▶ BrokerAdapter
                                                                    ▲                    │
                                                                    └── positions, P&L ◀─┘ (fills, reconciliation)
```

## Strategy contract (implemented)

- `StrategyMeta`: key, version, name, category, description, entry/exit/stop/take-profit rules, sizing, asset classes, timeframes, regimes, cost assumptions, default params (Section 6 fields).
- `target_position(bars, params) -> Series in [-1, 1]`: fraction of the maximum size the risk engine allows. Pure, no I/O.
- **No look-ahead**: the value at bar *t* may use bars up to and including *t* only. A generic test runs every registered strategy on a truncated and a full history and requires identical overlapping output.
- The backtester acts on a signal at the **next** bar's open, never the bar that produced it.
- Plugins register through the `apex.strategies` entry-point group, so strategies can live in a separate package.

## Lifecycle (implemented)

`experimental → backtesting → validation → paper_trading → approved`, with `suspended` and `rejected` reachable from any active state. A suspended strategy must re-enter `validation`; nothing reaches `approved` without passing `paper_trading`. Transitions are recorded in `strategy_status_changes`.

## Backtester (Phase 5)

- Two modes: a vectorised mode for parameter sweeps (NumPy, optionally vectorbt), and an event-driven mode that is the source of truth and shares code with paper trading so their fills agree.
- Costs from the instrument `CostModel`: commission per unit and bps, spread, slippage, funding per day held, plus configurable latency in bars and a square-root market-impact term for size relative to bar volume.
- Metrics: net return, CAGR, win rate, profit factor, expectancy, max drawdown, Sharpe, Sortino, Calmar, average win/loss, max consecutive losses, trade frequency, monthly consistency.
- Validation: anchored and rolling walk-forward, hold-out out-of-sample period locked until final evaluation, Monte Carlo (trade bootstrap and return block bootstrap), parameter-sensitivity heatmaps, and overfitting controls: Deflated Sharpe Ratio and Probability of Backtest Overfitting (CSCV), with the number of trials tracked per strategy family so multiple testing is accounted for.
- Leakage prevention: features computed with explicit lags; purged and embargoed cross-validation for ML; scaler/encoder fitting only on training folds.
- Every run writes an `experiments` row with data hashes, git commit, params and seed. Re-running it must reproduce identical metrics (test).

## Tournament (Phase 6)

Ranking score combines out-of-sample risk-adjusted return, Deflated Sharpe, drawdown, stability across walk-forward folds and regimes, and capacity after costs. Raw historical return is never the ranking key. Promotion gates (initial values, tuned later): minimum 100 OOS trades, DSR > 0.95, positive OOS expectancy after costs in at least 60% of folds, max drawdown within the risk profile, and at least 30 trading days of paper trading consistent with backtest expectations before `approved`.

## Risk service (Phase 9)

Independent module and process with its own config. Pre-trade checks: max risk per trade, max position size, max gross/net exposure, max leverage, max concurrent positions, correlation cap by cluster, daily/weekly loss, account drawdown, consecutive losses, duplicate order detection (idempotency key per signal), stale-data check (`is_stale`, implemented). Kill switch: flattens or freezes, persisted, and requires manual reset. Risk limits can only be tightened automatically, never loosened. No martingale or loss-recovery sizing.

## Execution service (Phase 9 paper, Phase 11 brokers)

Order state machine: `new → submitted → acknowledged → partially_filled → filled | cancelled | rejected | expired`. Every order carries a client order id derived from the signal so a restart cannot double-submit. Brackets (stop loss / take profit) are placed broker-side where supported. Start-up sequence: load state, connect broker, reconcile positions and open orders, compare, and stay halted if anything is uncertain. Paper broker uses the same fill model as the event-driven backtester.
