"""Robustness and overfitting controls (spec Sections 7 and 8).

* Walk-forward optimisation: optimise on a training window, evaluate the chosen
  parameters on the following unseen window, roll forward. Only out-of-sample results count.
* Hold-out split: keep the most recent data locked away until a final check.
* Monte Carlo: bootstrap trade P&L order to see the range of drawdowns luck alone produces.
* Parameter sensitivity: a robust edge should not depend on one exact parameter value.
* Probabilistic and Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2012/2014): the
  probability the true Sharpe exceeds a benchmark, deflated for the number of trials.
* Probability of Backtest Overfitting via combinatorially symmetric cross-validation
  (Bailey, Borwein, Lopez de Prado, Zhu, 2015).
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from apex.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from apex.domain.market import InstrumentSpec
from apex.strategies.base import Strategy
from apex.strategies.multi import MultiStrategy, align

# Walk-forward, PBO and sensitivity work for single- and multi-instrument strategies.
# For a MultiStrategy, ``bars`` is {symbol: frame} and ``spec`` is {symbol: spec}; the legs
# are aligned on common timestamps once, then sliced together by row position.
Bars = pd.DataFrame | dict[str, pd.DataFrame]
Spec = InstrumentSpec | dict[str, InstrumentSpec]


def _run(bars: Bars, strategy: Strategy, spec: Spec, params: dict,
         cfg: BacktestConfig | None) -> BacktestResult:
    if isinstance(strategy, MultiStrategy):
        from apex.backtest.portfolio import run_portfolio_backtest

        return run_portfolio_backtest(bars, strategy, spec, params, cfg)
    return run_backtest(bars, strategy, spec, params, cfg)


def _prepare(bars: Bars, strategy: Strategy) -> Bars:
    if isinstance(strategy, MultiStrategy):
        return align(bars)
    return bars.reset_index(drop=True)


def _nrows(bars: Bars) -> int:
    return len(next(iter(bars.values()))) if isinstance(bars, dict) else len(bars)


def _ts(bars: Bars) -> pd.Series:
    return next(iter(bars.values()))["ts"] if isinstance(bars, dict) else bars["ts"]


def _slice(bars: Bars, lo: int, hi: int) -> Bars:
    if isinstance(bars, dict):
        return {k: v.iloc[lo:hi].reset_index(drop=True) for k, v in bars.items()}
    return bars.iloc[lo:hi].reset_index(drop=True)

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


# ------------------------------------------------------------------ Sharpe statistics
def probabilistic_sharpe(sr: float, n_obs: int, skew: float = 0.0, kurtosis: float = 3.0,
                         sr_benchmark: float = 0.0) -> float:
    """P(true per-period Sharpe > benchmark). ``kurtosis`` is raw (normal = 3)."""
    if n_obs < 2:
        return float("nan")
    denom = 1 - skew * sr + (kurtosis - 1) / 4 * sr**2
    if denom <= 0:
        return float("nan")
    return _N.cdf((sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum Sharpe among ``n_trials`` independent zero-skill trials."""
    if n_trials <= 1:
        return 0.0
    a = _N.inv_cdf(1 - 1 / n_trials)
    b = _N.inv_cdf(1 - 1 / (n_trials * math.e))
    return math.sqrt(sr_variance) * ((1 - EULER_GAMMA) * a + EULER_GAMMA * b)


def null_sharpe_variance(n_obs: int) -> float:
    """Variance of a zero-skill per-period Sharpe estimate over ``n_obs`` returns."""
    return 1.0 / (n_obs - 1) if n_obs > 1 else 0.0


def deflated_sharpe(returns: pd.Series, n_trials: int,
                    sr_variance: float | None = None) -> dict[str, float]:
    """Deflated Sharpe Ratio of a per-period return series selected from ``n_trials``.

    ``sr_variance`` is the variance of the per-period Sharpe estimates across the trials.
    By default each trial is taken to be judged over as many returns as this series
    (``null_sharpe_variance``), so the bar falls as the evidence grows: more history makes
    a real edge easier to confirm, never harder.
    """
    r = returns.dropna()
    sd = r.std(ddof=1)
    sr = float(r.mean() / sd) if len(r) > 1 and sd > 0 else 0.0
    skew = float(r.skew()) if len(r) > 2 else 0.0
    kurt = float(r.kurt()) + 3.0 if len(r) > 3 else 3.0
    if sr_variance is None:
        sr_variance = null_sharpe_variance(len(r))
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    return {"sharpe_per_period": sr, "sr_benchmark": sr0, "n_trials": n_trials,
            "n_obs": len(r), "skew": skew, "kurtosis": kurt, "sr_variance": sr_variance,
            "dsr": probabilistic_sharpe(sr, len(r), skew, kurt, sr0),
            "psr_vs_zero": probabilistic_sharpe(sr, len(r), skew, kurt, 0.0)}


# ------------------------------------------------------------------------------- PBO
def probability_of_backtest_overfitting(perf: pd.DataFrame, n_splits: int = 8) -> dict:
    """CSCV. ``perf``: rows = time periods, columns = strategy configurations (returns).

    For every way of choosing half the blocks as in-sample, pick the best configuration
    in-sample and find its relative rank out-of-sample. PBO = share of splits where the
    in-sample winner ranks below the out-of-sample median.
    """
    if perf.shape[1] < 2:
        raise ValueError("need at least two configurations")
    if n_splits % 2 or n_splits < 2:
        raise ValueError("n_splits must be an even number >= 2")
    blocks = np.array_split(np.arange(len(perf)), n_splits)
    values = perf.to_numpy(float)

    def sharpe(rows: np.ndarray) -> np.ndarray:
        x = values[rows]
        sd = x.std(axis=0, ddof=1)
        return np.where(sd > 0, x.mean(axis=0) / np.where(sd > 0, sd, 1), 0.0)

    logits = []
    for is_blocks in itertools.combinations(range(n_splits), n_splits // 2):
        is_rows = np.concatenate([blocks[i] for i in is_blocks])
        oos_rows = np.concatenate([blocks[i] for i in range(n_splits) if i not in is_blocks])
        best = int(np.argmax(sharpe(is_rows)))
        oos = sharpe(oos_rows)
        rank = (oos < oos[best]).sum() + 0.5 * ((oos == oos[best]).sum() - 1)
        w = (rank + 1) / (len(oos) + 1)  # relative rank in (0, 1)
        logits.append(math.log(w / (1 - w)))
    logits_arr = np.array(logits)
    return {"pbo": float((logits_arr <= 0).mean()), "n_combinations": len(logits),
            "median_logit": float(np.median(logits_arr))}


# -------------------------------------------------------------------- Monte Carlo
def monte_carlo_trades(trade_pnls: list[float], initial_capital: float, n: int = 2000,
                       seed: int = 7) -> dict[str, Any]:
    """Bootstrap trade P&L (with replacement) to estimate the spread of outcomes."""
    pnl = np.asarray(trade_pnls, float)
    if len(pnl) == 0:
        return {"n": 0}
    rng = np.random.default_rng(seed)
    samples = rng.choice(pnl, size=(n, len(pnl)), replace=True)
    equity = initial_capital + np.cumsum(samples, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.full((n, 1), initial_capital), equity],
                                                axis=1), axis=1)[:, 1:]
    mdd = ((equity - peak) / peak).min(axis=1)
    final = equity[:, -1] / initial_capital - 1
    pct = [5, 25, 50, 75, 95]
    return {"n": n, "seed": seed, "trades": len(pnl),
            "final_return_pct": dict(zip(pct, np.percentile(final, pct).round(6).tolist(),
                                         strict=True)),
            "max_drawdown_pct": dict(zip(pct, np.percentile(mdd, pct).round(6).tolist(),
                                         strict=True)),
            "prob_loss": float((final < 0).mean())}


# -------------------------------------------------------------------- walk-forward
@dataclass
class Fold:
    train_start: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    params: dict
    is_score: float
    oos_metrics: dict


@dataclass
class WalkForwardResult:
    folds: list[Fold]
    oos_returns: pd.Series  # stitched per-bar OOS returns
    n_trials: int
    trial_sharpes: list[float] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    # simply holding the market over the same unseen stretches (single markets only)
    buy_hold_returns: pd.Series | None = None


def param_grid(grid: dict[str, list]) -> list[dict]:
    keys = list(grid)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*grid.values())]


def _score(res: BacktestResult, objective: str) -> float:
    v = res.metrics.get(objective)
    return float(v) if v is not None and math.isfinite(v) else -math.inf


def _segment(bars, strategy, spec, params, cfg, start_i, end_i, warmup):
    """Backtest rows [start_i:end_i] using `warmup` earlier rows for indicators only."""
    lo = max(0, start_i - warmup)
    seg = _slice(bars, lo, end_i)
    # strategy flat during warm-up so no position is carried into the window
    offset = start_i - lo

    if isinstance(strategy, MultiStrategy):
        class _MaskedMulti(MultiStrategy):
            meta, legs = strategy.meta, strategy.legs

            def target_weights(self, b, p):
                w = strategy.target_weights(b, p).copy()
                w.iloc[:offset] = 0.0
                return w

        masked = _MaskedMulti()
    else:
        class _Masked(Strategy):
            meta = strategy.meta

            def target_position(self, b, p):
                pos = strategy.target_position(b, p).copy()
                pos.iloc[:offset] = 0.0
                return pos

        masked = _Masked()
    res = _run(seg, masked, spec, params, cfg)
    eq = res.equity.iloc[offset:]
    return res, eq


def _signal(bars: Bars, strategy: Strategy, params: dict) -> pd.Series | pd.DataFrame:
    """The strategy's signal over the whole history, worked out once.

    Every strategy is causal: a signal never changes when later bars are added (the
    look-ahead tests in tests/test_strategies_research_api.py and tests/test_validation_multi.py
    check each one). So a window's slice of this equals what the strategy says when it is
    given every bar up to the end of that window, and a walk-forward no longer has to replay
    the whole history before each unseen window."""
    if isinstance(strategy, MultiStrategy):
        return strategy.target_weights({k: v.copy() for k, v in bars.items()}, params)
    return strategy.target_position(bars.copy(), params)


def _replay(strategy: Strategy, part: pd.Series | pd.DataFrame) -> Strategy:
    """A stand-in for ``strategy`` that gives back an already worked-out signal."""
    if isinstance(strategy, MultiStrategy):
        class _ReplayMulti(MultiStrategy):
            meta, legs = strategy.meta, strategy.legs

            def target_weights(self, b, p):
                return part

        return _ReplayMulti()

    class _Replay(Strategy):
        meta = strategy.meta

        def target_position(self, b, p):
            return part

    return _Replay()


def _window(bars, strategy, spec, params, cfg, signal, start_i, end_i):
    """Backtest rows [start_i:end_i] on the whole-history signal, flat on entry. Same result
    as replaying every earlier bar with the strategy held flat, at a fraction of the cost."""
    part = signal.iloc[start_i:end_i].reset_index(drop=True)
    res = _run(_slice(bars, start_i, end_i), _replay(strategy, part), spec, params, cfg)
    return res, res.equity


def walk_forward(bars: Bars, strategy: Strategy, spec: Spec,
                 grid: dict[str, list], *, train_bars: int, test_bars: int,
                 warmup: int = 0, anchored: bool = False, objective: str = "sharpe",
                 config: BacktestConfig | None = None) -> WalkForwardResult:
    """Walk-forward test. Each training window is backtested on its own (indicators warm up
    from ``warmup`` earlier bars); each unseen window uses the strategy's signal given all
    the history before it, so nothing it could not have known is used."""
    cfg = config or BacktestConfig()
    combos = [strategy.params(c) for c in param_grid(grid)]
    bars = _prepare(bars, strategy)
    n, ts = _nrows(bars), _ts(bars)
    folds, oos_parts, trial_sharpes, hold_parts = [], [], [], []
    signals: dict[str, pd.Series | pd.DataFrame] = {}  # per settings, worked out once
    start = train_bars
    while start + test_bars <= n:
        tr_lo = 0 if anchored else start - train_bars
        scored = []
        # With a single setting there is nothing to choose, so the training stretch is not
        # backtested at all (it used to be, only to report a tuning-data Sharpe, at several
        # times the cost of the unseen windows). The unseen results are the same either way.
        for c in combos if len(combos) > 1 else []:
            try:
                res, _ = _segment(bars, strategy, spec, c, cfg, tr_lo, start, warmup)
                s = _score(res, objective)
            except ValueError:
                s = -math.inf
            scored.append(s)
            if math.isfinite(s):
                trial_sharpes.append(s)
        best_i = int(np.argmax(scored)) if scored else 0
        best = combos[best_i]
        key = json.dumps(best, sort_keys=True, default=str)
        if key not in signals:
            signals[key] = _signal(bars, strategy, best)
        res, eq = _window(bars, strategy, spec, best, cfg, signals[key], start,
                          start + test_bars)
        oos_parts.append(eq.pct_change().fillna(0.0))
        if not isinstance(bars, dict):  # a benchmark for single markets: buy and hold
            close = bars["close"].iloc[start:start + test_bars]
            hold_parts.append(pd.Series(close.to_numpy(float), index=pd.DatetimeIndex(
                ts.iloc[start:start + test_bars])).pct_change().fillna(0.0))
        folds.append(Fold(ts.iloc[tr_lo], ts.iloc[start],
                          ts.iloc[start + test_bars - 1], best,
                          scored[best_i] if scored else math.nan,
                          {k: res.metrics.get(k) for k in
                           ("total_return", "sharpe", "max_drawdown", "trades", "win_rate")}))
        start += test_bars
    if not folds:
        raise ValueError("not enough bars for one train+test window")
    oos = pd.concat(oos_parts)
    wf = WalkForwardResult(folds, oos, n_trials=len(combos) * len(folds),
                           trial_sharpes=trial_sharpes,
                           buy_hold_returns=pd.concat(hold_parts) if hold_parts else None)
    wf.summary = summarise_walk_forward(wf)
    return wf


def summarise_walk_forward(wf: WalkForwardResult) -> dict:
    oos = wf.oos_returns
    eq = (1 + oos).cumprod()
    tr = [f.oos_metrics.get("total_return") or 0.0 for f in wf.folds]
    is_scores = [f.is_score for f in wf.folds if math.isfinite(f.is_score)]
    oos_sharpes = [f.oos_metrics.get("sharpe") for f in wf.folds]
    oos_sharpes = [s for s in oos_sharpes if s is not None]
    # Every setting tried on every stretch counts as a try (on the safe side), and each try
    # is judged like this one: over the whole unseen history. (The training-stretch Sharpes
    # in wf.trial_sharpes are far noisier, being over much less data, so they would set the
    # bar by the training length and make more history count against a real edge.)
    dsr = deflated_sharpe(oos, max(wf.n_trials, 1))
    hold = wf.buy_hold_returns
    held = (1 + hold).cumprod() if hold is not None and len(hold) else None
    return {
        "folds": len(wf.folds), "n_trials": wf.n_trials,
        "oos_total_return": float(eq.iloc[-1] - 1) if len(eq) else None,
        "oos_max_drawdown": float((eq / eq.cummax() - 1).min()) if len(eq) else None,
        "oos_buy_hold_return": float(held.iloc[-1] - 1) if held is not None else None,
        "oos_buy_hold_max_drawdown": (float((held / held.cummax() - 1).min())
                                      if held is not None else None),
        "pct_profitable_folds": float(np.mean([r > 0 for r in tr])),
        "mean_is_sharpe": float(np.mean(is_scores)) if is_scores else None,
        "mean_oos_sharpe": float(np.mean(oos_sharpes)) if oos_sharpes else None,
        "walk_forward_efficiency": (float(np.mean(oos_sharpes) / np.mean(is_scores))
                                    if is_scores and oos_sharpes and np.mean(is_scores) > 0
                                    else None),
        "deflated_sharpe": dsr["dsr"], "psr_vs_zero": dsr["psr_vs_zero"],
        "dsr_inputs": {k: dsr[k] for k in ("sharpe_per_period", "n_obs", "skew", "kurtosis",
                                            "sr_variance")},
        "param_stability": _param_stability([f.params for f in wf.folds]),
    }


def _param_stability(params: list[dict]) -> float:
    """Share of folds that chose the most common parameter set (1.0 = always the same)."""
    # JSON text, so list-valued settings (e.g. event dates) can be compared too
    keys = [json.dumps(p, sort_keys=True, default=str) for p in params]
    return max(keys.count(k) for k in set(keys)) / len(keys) if keys else 0.0


# ----------------------------------------------------------------------- sensitivity
def sensitivity(bars: Bars, strategy: Strategy, spec: Spec,
                base: dict, param: str, values: list, objective: str = "sharpe",
                config: BacktestConfig | None = None) -> pd.DataFrame:
    rows = []
    for v in values:
        p = strategy.params({**base, param: v})
        try:
            m = _run(bars, strategy, spec, p, config).metrics
            rows.append({param: v, objective: m.get(objective), "trades": m.get("trades"),
                         "total_return": m.get("total_return")})
        except ValueError as exc:
            rows.append({param: v, objective: None, "error": str(exc)})
    return pd.DataFrame(rows)


def returns_matrix(bars: Bars, strategy: Strategy, spec: Spec,
                   grid: dict[str, list], config: BacktestConfig | None = None) -> pd.DataFrame:
    """Per-bar returns of every configuration in ``grid`` (input for PBO)."""
    cols = {}
    for c in param_grid(grid):
        try:
            res = _run(bars, strategy, spec, strategy.params(c), config)
        except ValueError:
            continue
        cols[str(c)] = res.equity.pct_change().fillna(0.0).to_numpy()
    return pd.DataFrame(cols)


def split_holdout(bars: pd.DataFrame, fraction: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(research, holdout). Use the hold-out once, for the final decision only."""
    if not 0 < fraction < 1:
        raise ValueError("fraction must be in (0, 1)")
    cut = int(len(bars) * (1 - fraction))
    return bars.iloc[:cut].reset_index(drop=True), bars.iloc[cut:].reset_index(drop=True)
