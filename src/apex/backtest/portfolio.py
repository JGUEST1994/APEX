"""Portfolio backtester for multi-instrument strategies (spec H38-H41, H05).

Same execution model as the single-instrument engine: weights decided at bar t are filled
at the OPEN of bar t + ``latency_bars``, orders go out only when the target weights change,
every fill pays the instrument's commission, spread and slippage, open positions pay
funding, and equity is marked at each close. Legs are aligned on common timestamps first.
A "trade" is one round trip of the whole book (from flat to flat).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from apex.backtest.engine import BacktestConfig, BacktestResult, Trade, fill_cost
from apex.domain.market import InstrumentSpec
from apex.strategies.multi import MultiStrategy, align


def run_portfolio_backtest(bars: dict[str, pd.DataFrame], strategy: MultiStrategy,
                           specs: dict[str, InstrumentSpec],
                           params: dict[str, Any] | None = None,
                           config: BacktestConfig | None = None) -> BacktestResult:
    cfg = config or BacktestConfig()
    p = strategy.params(params)
    if len(bars) != strategy.legs:
        raise ValueError(f"{strategy.meta.key} needs {strategy.legs} instruments")
    frames = align(bars)
    syms = list(frames)
    n = len(frames[syms[0]])
    if n < 2:
        raise ValueError("need at least 2 common bars")
    w = strategy.target_weights({s: f.copy() for s, f in frames.items()}, p)
    w = w.reindex(columns=syms).fillna(0.0).to_numpy(float)
    gross = np.abs(w).sum(axis=1, keepdims=True)
    w = np.where(gross > cfg.leverage, w * cfg.leverage / np.maximum(gross, 1e-12), w)

    ts = pd.DatetimeIndex(frames[syms[0]]["ts"])
    opens = np.column_stack([frames[s]["open"].to_numpy(float) for s in syms])
    closes = np.column_stack([frames[s]["close"].to_numpy(float) for s in syms])
    sp = [specs[s] for s in syms]
    mult = np.array([s.contract_multiplier for s in sp])
    cash, units = cfg.initial_capital, np.zeros(len(syms))
    equity, gross_units = np.empty(n), np.empty(n)
    fills: list[dict] = []
    trades: list[Trade] = []
    total_costs, last_equity = 0.0, cfg.initial_capital
    executed = np.zeros(len(syms))
    book: dict | None = None

    for i in range(n):
        if i > 0 and units.any():
            days = (ts[i] - ts[i - 1]).total_seconds() / 86400
            fund = sum(cfg.cost_multiplier * abs(units[k]) * opens[i, k] * mult[k]
                       * sp[k].costs.funding_bps_per_day / 1e4 * days
                       for k in range(len(syms)))
            cash -= fund
            total_costs += fund
            if book:
                book["costs"] += fund
        j = i - cfg.latency_bars
        if j >= 0 and not np.array_equal(w[j], executed):
            executed = w[j].copy()
            eq_before = cash + float((units * opens[i] * mult).sum())
            if book is not None and not np.array_equal(np.sign(w[j]), book["signs"]):
                # flat or reversed: the current round trip ends at this open; the costs of
                # the closing fills (below) are charged to it
                closing, book["signs"] = book, None
            else:
                closing = None
            for k in range(len(syms)):
                lot = sp[k].lot_size
                raw = w[j, k] * last_equity / (opens[i, k] * mult[k])
                want = math.copysign(math.floor(abs(raw) / lot) * lot, raw) if raw else 0.0
                delta = want - units[k]
                if abs(delta) <= 1e-12:
                    continue
                cost = fill_cost(sp[k], delta, opens[i, k], cfg.cost_multiplier)
                cash -= delta * opens[i, k] * mult[k] + cost
                total_costs += cost
                prev, units[k] = units[k], want
                fills.append({"ts": ts[i], "symbol": syms[k], "units": delta,
                              "price": opens[i, k], "cost": cost})
                if closing is not None:  # exit share of this fill belongs to the old trade
                    flips = prev != 0 and (want == 0 or np.sign(want) != np.sign(prev))
                    share = abs(prev) / abs(delta) if flips else 0.0
                    closing["costs"] += cost * share
                    closing["exit_cost"] = closing.get("exit_cost", 0.0) + cost * share
                    cost -= cost * share
                    book = None
                if book is None and w[j].any():
                    book = {"entry_ts": ts[i], "entry_i": i, "costs": 0.0,
                            "equity_start": None, "signs": np.sign(w[j]),
                            "side": int(np.sign(w[j, 0]) or 1), "max_units": 0.0}
                if book is not None:
                    book["costs"] += cost
                    book["entry_cost"] = book.get("entry_cost", 0.0) + cost
            if closing is not None:
                eq_exit = eq_before - closing.get("exit_cost", 0.0)
                trades.append(_close(closing, ts[i], eq_exit, i))
                if book is not None:
                    book["equity_start"] = eq_exit
            elif book is not None and book["equity_start"] is None:
                book["equity_start"] = eq_before
            if book is not None:
                book["max_units"] = max(book["max_units"], float(np.abs(units).sum()))
        equity[i] = cash + float((units * closes[i] * mult).sum())
        gross_units[i] = float(np.abs(units).sum())
        last_equity = equity[i]
        if last_equity <= 0:
            equity[i:], gross_units[i:] = last_equity, gross_units[i]
            break
    if book is not None:
        trades.append(_close(book, ts[n - 1], equity[n - 1], n - 1, at_end=True))

    result = BacktestResult(
        equity=pd.Series(equity, index=ts, name="equity"),
        position=pd.Series(gross_units, index=ts, name="gross_units"),
        fills=pd.DataFrame(fills, columns=["ts", "symbol", "units", "price", "cost"]),
        trades=trades, total_costs=total_costs, config=cfg)
    from apex.backtest.metrics import compute_metrics

    result.metrics = compute_metrics(result)
    result.metrics["symbols"] = syms
    return result


def _close(b: dict, ts: pd.Timestamp, equity_end: float, i: int,
           at_end: bool = False) -> Trade:
    return Trade(entry_ts=b["entry_ts"], exit_ts=ts, side=b["side"], max_units=b["max_units"],
                 entry_price=0.0, exit_price=0.0, pnl=equity_end - b["equity_start"],
                 costs=b["costs"], bars_held=i - b["entry_i"], closed_at_end=at_end)
