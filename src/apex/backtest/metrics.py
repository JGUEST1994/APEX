"""Performance metrics (spec Section 7). Pure functions of the equity curve and trades."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from apex.backtest.engine import BacktestResult, Trade

SECONDS_PER_YEAR = 365.25 * 86400


def periods_per_year(index: pd.DatetimeIndex) -> float:
    """Observed bars per year, which accounts for each market's trading calendar."""
    if len(index) < 2:
        return 0.0
    span = (index[-1] - index[0]).total_seconds()
    return (len(index) - 1) / (span / SECONDS_PER_YEAR) if span > 0 else 0.0


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float(((equity - peak) / peak).min()) if len(equity) else 0.0


def max_consecutive(flags: list[bool]) -> int:
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


def _f(x: float) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), 6)


def trade_stats(trades: list[Trade]) -> dict[str, Any]:
    pnl = np.array([t.pnl for t in trades], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    return {
        "trades": len(pnl),
        "win_rate": _f(len(wins) / len(pnl)) if len(pnl) else None,
        "profit_factor": _f(gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy": _f(pnl.mean()) if len(pnl) else None,
        "avg_win": _f(wins.mean()) if len(wins) else None,
        "avg_loss": _f(losses.mean()) if len(losses) else None,
        "max_consecutive_losses": max_consecutive([p < 0 for p in pnl]),
        "avg_bars_held": _f(np.mean([t.bars_held for t in trades])) if trades else None,
    }


def return_stats(equity: pd.Series) -> dict[str, Any]:
    ppy = periods_per_year(equity.index)
    rets = equity.pct_change().dropna()
    total = equity.iloc[-1] / equity.iloc[0] - 1
    years = (equity.index[-1] - equity.index[0]).total_seconds() / SECONDS_PER_YEAR
    growth = equity.iloc[-1] / equity.iloc[0]
    cagr = growth ** (1 / years) - 1 if years > 0 and growth > 0 else float("nan")
    vol = rets.std(ddof=1) * math.sqrt(ppy) if len(rets) > 1 else float("nan")
    sd = rets.std(ddof=1) if len(rets) > 1 else 0.0
    sharpe = rets.mean() / sd * math.sqrt(ppy) if sd > 0 else float("nan")
    downside = rets[rets < 0]
    dd_dev = math.sqrt((downside ** 2).sum() / len(rets)) if len(rets) else float("nan")
    sortino = rets.mean() / dd_dev * math.sqrt(ppy) if dd_dev and dd_dev > 0 else float("nan")
    mdd = max_drawdown(equity)
    calmar = cagr / abs(mdd) if mdd < 0 and math.isfinite(cagr) else float("nan")
    monthly = equity.resample("ME").last().pct_change().dropna()
    return {
        "total_return": _f(total), "cagr": _f(cagr), "ann_volatility": _f(vol),
        "sharpe": _f(sharpe), "sortino": _f(sortino), "max_drawdown": _f(mdd),
        "calmar": _f(calmar), "periods_per_year": _f(ppy), "years": _f(years),
        "pct_positive_months": _f((monthly > 0).mean()) if len(monthly) else None,
    }


def compute_metrics(result: BacktestResult) -> dict[str, Any]:
    eq = result.equity
    m = {**return_stats(eq), **trade_stats(result.trades)}
    years = m["years"] or 0
    m["trades_per_year"] = _f(m["trades"] / years) if years else None
    m["exposure"] = _f((result.position != 0).mean())
    m["total_costs"] = _f(result.total_costs)
    m["final_equity"] = _f(eq.iloc[-1])
    m["note"] = "Sharpe/Sortino are not adjusted for multiple testing; see Phase 5 DSR."
    return m
