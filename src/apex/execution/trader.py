"""Paper trading loop: strategy signal -> sizing -> independent risk check -> paper broker.

``PaperTrader.on_bar`` is called once per completed bar, in order. It only ever sees bars
up to the current one (the strategy is re-run on that history), so replaying stored bars
through it is the same code path a live paper feed uses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from apex.domain.market import InstrumentSpec
from apex.execution.orders import Order, client_order_id
from apex.execution.paper import Fill, PaperBroker, Portfolio
from apex.risk.correlation import correlation_clusters
from apex.risk.service import AccountState, OrderIntent, RiskService
from apex.strategies import indicators as ind
from apex.strategies.base import Strategy
from apex.strategies.multi import MultiStrategy, align


@dataclass
class Event:
    ts: datetime
    kind: str  # fill | refused | kill_switch
    detail: dict = field(default_factory=dict)


class PaperTrader:
    """Runs one strategy on one or more instruments with shared cash and one risk service.

    Each instrument gets its own signal from its own bar history; sizing, the risk checks
    and the kill switch see the whole account. With several instruments, the correlated-
    exposure limit is fed with groups computed from trailing returns of the bars seen so far.

    A ``MultiStrategy`` (pairs, baskets, cross-asset) instead gets the aligned histories of
    all its legs, in the order the instruments were given, and returns a target weight per
    leg (signed notional / equity). It only trades at timestamps where every leg has a bar;
    each leg's order then goes through the same sizing caps, risk checks and kill switch.
    """

    def __init__(self, strategy: Strategy, spec: InstrumentSpec | list[InstrumentSpec],
                 risk: RiskService, initial_capital: float = 100_000.0,
                 params: dict | None = None, stop_atr: float = 2.0, history: int = 600,
                 account: str = "", corr_window: int = 120,
                 rebalance_band: float = 0.1) -> None:
        specs = spec if isinstance(spec, list) else [spec]
        self.multi = isinstance(strategy, MultiStrategy)
        if self.multi:
            strategy.check_legs(len(specs))
        self.legs = [s.symbol for s in specs]
        self.specs = {s.symbol: s for s in specs}
        self.spec = specs[0]  # the primary instrument (single-instrument accounts)
        self.strategy, self.risk, self.account = strategy, risk, account
        self.params = strategy.params(params)
        self.broker = PaperBroker(dict(self.specs))
        self.pf = Portfolio(cash=initial_capital)
        self.stop_atr, self.history, self.corr_window = stop_atr, history, corr_window
        self.rebalance_band = rebalance_band  # multi: skip leg changes under this fraction
        self.hist: dict[str, list[dict]] = {s: [] for s in self.specs}
        self.events: list[Event] = []
        self.peak = self.day_start = initial_capital
        self.day: object = None

    @property
    def bars(self) -> list[dict]:
        """Bar history of the primary instrument."""
        return self.hist[self.spec.symbol]

    @bars.setter
    def bars(self, rows: list[dict]) -> None:
        self.hist[self.spec.symbol] = rows

    # -- helpers
    def last_prices(self) -> dict[str, float]:
        return {s: h[-1]["close"] for s, h in self.hist.items() if h}

    def _account(self) -> AccountState:
        prices = self.last_prices()
        specs = {s: self.specs[s] for s in prices}
        held = {s for s, u in self.pf.units.items() if u}
        missing = held - set(prices)
        if missing:  # a held instrument with no bar yet (restart): fall back to cost
            prices |= {s: self.pf.avg_cost.get(s, 0.0) for s in missing}
            specs |= {s: self.specs[s] for s in missing}
        eq = self.pf.equity(prices, specs)
        self.peak = max(self.peak, eq)
        return AccountState(eq, self.day_start, self.peak, self.pf.notionals(prices, specs),
                            self.pf.consecutive_losses, self._clusters())

    def _clusters(self) -> dict[str, str]:
        if len(self.specs) < 2:
            return {}
        closes = {s: pd.Series({b["ts"]: b["close"] for b in h[-self.corr_window - 1:]})
                  for s, h in self.hist.items() if h}
        rets = pd.DataFrame(closes).sort_index().pct_change(fill_method=None)
        return correlation_clusters(rets)

    def _size(self, spec: InstrumentSpec, target: float, price: float, stop_dist: float,
              equity: float) -> float:
        lim, m = self.risk.limits, spec.contract_multiplier
        by_notional = lim.max_position_notional * equity / (price * m)
        by_risk = lim.max_risk_per_trade * equity / (stop_dist * m) if stop_dist > 0 else 0.0
        units = abs(target) * min(by_notional, by_risk) * 0.999  # stay inside the limits
        lot = spec.lot_size
        return math.copysign(math.floor(units / lot) * lot, target) if target else 0.0

    def _submit(self, intent: OrderIntent, acct: AccountState, ts: datetime,
                data_stale: bool) -> bool:
        decision = self.risk.check(intent, acct, data_stale=data_stale, now=ts)
        if decision:
            self.broker.submit(Order(intent.client_order_id, intent.symbol, intent.qty,
                                     intent.stop_price))
            # later instruments in the same bar are checked against the account as if
            # this order had filled, so one bar cannot breach a limit in several steps
            m = intent.qty * intent.price * intent.multiplier
            acct.positions[intent.symbol] = acct.positions.get(intent.symbol, 0.0) + m
        else:
            self.events.append(Event(ts, "refused", {"symbol": intent.symbol,
                                                     "qty": intent.qty,
                                                     "reasons": list(decision.reasons)}))
        return bool(decision)

    # -- main entry points
    def on_bar(self, bar: dict, *, data_stale: bool = False) -> list[Fill]:
        """Single-instrument accounts: one bar of the primary instrument."""
        return self.on_bars({self.spec.symbol: bar}, data_stale=data_stale)

    def on_bars(self, bars: dict[str, dict], *, data_stale: bool = False) -> list[Fill]:
        """One timestamp: the bars that closed at it, keyed by symbol (instruments whose
        market was closed are simply absent)."""
        syms = sorted(bars)
        ts = bars[syms[0]]["ts"]
        fills = []
        for sym in syms:  # orders from the previous bar fill at this bar's open
            for f in self.broker.on_bar_open(sym, bars[sym]["open"]):
                self.pf.apply(f, self.specs[sym])
                fills.append(f)
                self.events.append(Event(ts, "fill", {"symbol": sym, "qty": f.qty,
                                                      "price": f.price, "cost": f.cost}))
        for sym in syms:
            self.hist[sym].append(bars[sym])
            del self.hist[sym][:-self.history]
        day = pd.Timestamp(ts).date()
        acct = self._account()
        if day != self.day:
            self.day, self.day_start = day, acct.equity
            acct.day_start_equity = acct.equity
        was = self.risk.kill.engaged
        self.risk.monitor(acct, ts)
        if self.risk.kill.engaged and not was:
            self.events.append(Event(ts, "kill_switch", {"reason": self.risk.kill.reason}))
        if self.multi:
            if set(syms) == set(self.legs):  # a leg's market is closed: hold
                self._trade_multi(bars, acct, ts, data_stale)
        else:
            for sym in syms:
                self._trade(sym, bars[sym], acct, ts, data_stale)
        return fills

    def _stop_dist(self, df: pd.DataFrame, close: float) -> float:
        atr = ind.atr(df, 14).iloc[-1]
        return self.stop_atr * (atr if atr == atr else close * 0.02)

    def _trade_multi(self, bars: dict[str, dict], acct: AccountState, ts: datetime,
                     data_stale: bool) -> None:
        frames = align({s: pd.DataFrame(self.hist[s]) for s in self.legs})
        if len(frames[self.legs[0]]) == 0 or frames[self.legs[0]]["ts"].iloc[-1] != ts:
            return
        w = self.strategy.target_weights(frames, self.params).iloc[-1]
        for sym in self.legs:
            spec, bar = self.specs[sym], bars[sym]
            weight = 0.0 if self.risk.kill.engaged else float(np.nan_to_num(w.get(sym, 0.0)))
            stop_dist = self._stop_dist(frames[sym], bar["close"])
            m = spec.contract_multiplier
            target_units = weight * acct.equity / (bar["close"] * m)
            # the same caps as single-instrument sizing, applied to the leg's weight
            cap = abs(self._size(spec, 1.0, bar["close"], stop_dist, acct.equity))
            units = min(abs(target_units), cap)
            want = math.copysign(math.floor(units / spec.lot_size) * spec.lot_size, weight)
            have = self.pf.units.get(sym, 0.0)
            if (have and want and (have > 0) == (want > 0)
                    and abs(want - have) <= self.rebalance_band * abs(have)):
                continue  # drift inside the band: no order (stateless, so restart-safe)
            self._rebalance(sym, bar, want, stop_dist, acct, ts, data_stale)

    def _trade(self, sym: str, bar: dict, acct: AccountState, ts: datetime,
               data_stale: bool) -> None:
        spec = self.specs[sym]
        df = pd.DataFrame(self.hist[sym])
        target = float(self.strategy.target_position(df, self.params).iloc[-1])
        if self.risk.kill.engaged:
            target = 0.0  # flatten; the risk service only lets reducing orders through
        stop_dist = self._stop_dist(df, bar["close"])
        want = self._size(spec, target, bar["close"], stop_dist, acct.equity)
        self._rebalance(sym, bar, want, stop_dist, acct, ts, data_stale)

    def _rebalance(self, sym: str, bar: dict, want: float, stop_dist: float,
                   acct: AccountState, ts: datetime, data_stale: bool) -> None:
        spec = self.specs[sym]
        have = self.pf.units.get(sym, 0.0)
        qty = want - have
        if abs(qty) < spec.lot_size / 2:
            return
        stop = bar["close"] - math.copysign(stop_dist, want) if want else None
        coid = client_order_id(self.strategy.meta.key, sym, ts, self.account)
        m = spec.contract_multiplier
        if have and (want == 0 or (want > 0) != (have > 0)):
            # Exit or reversal: close first as its own (always-allowed) reducing order, so
            # a refused new entry can never leave the old position open.
            self._submit(OrderIntent(coid + "-x", sym, -have, bar["close"], m, None), acct,
                         ts, data_stale)
            if not want:
                return
            acct.positions[sym] = 0.0
            qty = want
        self._submit(OrderIntent(coid, sym, qty, bar["close"], m, stop), acct, ts, data_stale)
