"""Bar-based event-driven backtester (Phase 5 core).

Execution model (deliberately conservative):
  * The strategy sees bars up to and including bar t and returns a target position in
    [-1, 1] (fraction of the maximum position).
  * The order is filled at the OPEN of bar t + ``latency_bars`` (default 1). Never on the
    bar that produced the signal, so there is no look-ahead.
  * Max position = ``leverage`` x current equity in notional terms, rounded down to the
    instrument's lot size. Orders are sent only when the target changes, so the engine
    does not churn (and pay costs) re-sizing an unchanged position every bar.
  * Every fill pays commission (per unit and bps), half-spread and slippage from the
    instrument ``CostModel``; open positions pay funding per calendar day held.
  * Equity is marked to market at each bar's close.

Not modelled yet (tracked in docs/STATUS.md): margin calls, partial fills, intrabar
stop/limit orders, market impact beyond the bps slippage term, futures roll.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from apex.domain.market import InstrumentSpec
from apex.strategies.base import Strategy


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 100_000.0
    leverage: float = 1.0
    latency_bars: int = 1
    cost_multiplier: float = 1.0  # stress-test costs, e.g. 2.0 = double all costs

    def __post_init__(self) -> None:
        if self.latency_bars < 1:
            raise ValueError("latency_bars must be >= 1 (no same-bar fills)")
        if self.initial_capital <= 0 or self.leverage <= 0:
            raise ValueError("initial_capital and leverage must be positive")


@dataclass
class Trade:
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    side: int  # +1 long, -1 short
    max_units: float
    entry_price: float
    exit_price: float
    pnl: float  # net of all costs
    costs: float
    bars_held: int
    closed_at_end: bool = False


@dataclass
class BacktestResult:
    equity: pd.Series  # indexed by bar ts, marked at close
    position: pd.Series  # units held at each bar's close
    fills: pd.DataFrame
    trades: list[Trade]
    total_costs: float
    config: BacktestConfig
    metrics: dict[str, Any] = field(default_factory=dict)


def fill_cost(spec: InstrumentSpec, units: float, price: float, mult: float = 1.0) -> float:
    c = spec.costs
    notional = abs(units) * price * spec.contract_multiplier
    per_unit = abs(units) * c.commission_per_unit
    if c.commission_max_bps is not None:
        per_unit = min(per_unit, notional * c.commission_max_bps / 1e4)
    bps = c.commission_bps + c.spread_bps + c.slippage_bps
    return mult * (per_unit + notional * bps / 1e4)


def run_backtest(bars: pd.DataFrame, strategy: Strategy, spec: InstrumentSpec,
                 params: dict[str, Any] | None = None,
                 config: BacktestConfig | None = None) -> BacktestResult:
    cfg = config or BacktestConfig()
    p = strategy.params(params)
    bars = bars.reset_index(drop=True)
    if len(bars) < 2:
        raise ValueError("need at least 2 bars")
    target = strategy.target_position(bars.copy(), p).reindex(bars.index).fillna(0.0)
    target = target.clip(-1.0, 1.0).to_numpy()

    opens, closes = bars["open"].to_numpy(float), bars["close"].to_numpy(float)
    ts = pd.DatetimeIndex(bars["ts"])
    mult, lot = spec.contract_multiplier, spec.lot_size
    cash, units = cfg.initial_capital, 0.0
    equity = np.empty(len(bars))
    pos = np.empty(len(bars))
    fills: list[dict] = []
    trades: list[Trade] = []
    total_costs = 0.0
    open_trade: dict | None = None
    last_equity = cfg.initial_capital
    executed_target = 0.0

    for i in range(len(bars)):
        # 1) funding on the position carried into this bar
        if i > 0 and units:
            days = (ts[i] - ts[i - 1]).total_seconds() / 86400
            fund = (cfg.cost_multiplier * abs(units) * opens[i] * mult
                    * spec.costs.funding_bps_per_day / 1e4 * days)
            cash -= fund
            total_costs += fund
            if open_trade:
                open_trade["costs"] += fund

        # 2) execute the order decided `latency_bars` ago, at this bar's open
        j = i - cfg.latency_bars
        if j >= 0 and target[j] != executed_target:
            executed_target = target[j]
            max_units = cfg.leverage * last_equity / (opens[i] * mult)
            want = math.floor(abs(target[j]) * max_units / lot) * lot * np.sign(target[j])
            delta = want - units
            if abs(delta) > 1e-12:
                cost = fill_cost(spec, delta, opens[i], cfg.cost_multiplier)
                equity_before = cash + units * opens[i] * mult
                cash -= delta * opens[i] * mult + cost
                total_costs += cost
                fills.append({"ts": ts[i], "units": delta, "price": opens[i], "cost": cost})
                prev, units = units, want
                closing = prev != 0 and (units == 0 or np.sign(units) != np.sign(prev))
                if closing and open_trade:
                    # exit share of this fill's cost belongs to the closing trade
                    exit_cost = cost * (abs(prev) / abs(delta))
                    open_trade["costs"] += exit_cost
                    eq_after_exit = equity_before - exit_cost
                    trades.append(_close(open_trade, ts[i], opens[i], eq_after_exit, i))
                    open_trade = None
                    cost -= exit_cost
                    equity_before = eq_after_exit
                if units != 0 and open_trade is None:
                    open_trade = {"entry_ts": ts[i], "side": int(np.sign(units)),
                                  "entry_price": opens[i], "equity_start": equity_before,
                                  "costs": cost, "max_units": abs(units), "entry_i": i}
                elif open_trade is not None:
                    open_trade["costs"] += cost
                    open_trade["max_units"] = max(open_trade["max_units"], abs(units))

        # 3) mark to market at the close
        equity[i] = cash + units * closes[i] * mult
        pos[i] = units
        last_equity = equity[i]
        if last_equity <= 0:
            equity[i:] = last_equity
            pos[i:] = units
            break

    if open_trade is not None:
        trades.append(_close(open_trade, ts[len(bars) - 1], closes[-1], equity[-1],
                             len(bars) - 1, at_end=True))

    result = BacktestResult(
        equity=pd.Series(equity, index=ts, name="equity"),
        position=pd.Series(pos, index=ts, name="units"),
        fills=pd.DataFrame(fills, columns=["ts", "units", "price", "cost"]),
        trades=trades, total_costs=total_costs, config=cfg)
    from apex.backtest.metrics import compute_metrics

    result.metrics = compute_metrics(result)
    return result


def _close(t: dict, ts: pd.Timestamp, price: float, equity_end: float, i: int,
           at_end: bool = False) -> Trade:
    return Trade(entry_ts=t["entry_ts"], exit_ts=ts, side=t["side"], max_units=t["max_units"],
                 entry_price=t["entry_price"], exit_price=price,
                 pnl=equity_end - t["equity_start"], costs=t["costs"],
                 bars_held=i - t["entry_i"], closed_at_end=at_end)
