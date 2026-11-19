"""Paper trading loop: strategy signal -> sizing -> independent risk check -> paper broker.

``PaperTrader.on_bar`` is called once per completed bar, in order. It only ever sees bars
up to the current one (the strategy is re-run on that history), so replaying stored bars
through it is the same code path a live paper feed uses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from apex.domain.market import InstrumentSpec
from apex.execution.orders import Order, client_order_id
from apex.execution.paper import Fill, PaperBroker, Portfolio
from apex.risk.service import AccountState, OrderIntent, RiskService
from apex.strategies import indicators as ind
from apex.strategies.base import Strategy


@dataclass
class Event:
    ts: datetime
    kind: str  # fill | refused | kill_switch
    detail: dict = field(default_factory=dict)


class PaperTrader:
    def __init__(self, strategy: Strategy, spec: InstrumentSpec, risk: RiskService,
                 initial_capital: float = 100_000.0, params: dict | None = None,
                 stop_atr: float = 2.0, history: int = 600) -> None:
        self.strategy, self.spec, self.risk = strategy, spec, risk
        self.params = strategy.params(params)
        self.broker = PaperBroker({spec.symbol: spec})
        self.pf = Portfolio(cash=initial_capital)
        self.stop_atr, self.history = stop_atr, history
        self.bars: list[dict] = []
        self.events: list[Event] = []
        self.peak = self.day_start = initial_capital
        self.day: object = None

    # -- helpers
    def _account(self, price: float) -> AccountState:
        prices, specs = {self.spec.symbol: price}, {self.spec.symbol: self.spec}
        eq = self.pf.equity(prices, specs)
        self.peak = max(self.peak, eq)
        return AccountState(eq, self.day_start, self.peak, self.pf.notionals(prices, specs),
                            self.pf.consecutive_losses)

    def _size(self, target: float, price: float, stop_dist: float, equity: float) -> float:
        lim, m = self.risk.limits, self.spec.contract_multiplier
        by_notional = lim.max_position_notional * equity / (price * m)
        by_risk = lim.max_risk_per_trade * equity / (stop_dist * m) if stop_dist > 0 else 0.0
        units = abs(target) * min(by_notional, by_risk) * 0.999  # stay inside the limits
        lot = self.spec.lot_size
        return math.copysign(math.floor(units / lot) * lot, target) if target else 0.0

    # -- main entry point
    def on_bar(self, bar: dict, *, data_stale: bool = False) -> list[Fill]:
        ts, sym = bar["ts"], self.spec.symbol
        fills = self.broker.on_bar_open(sym, bar["open"])  # orders from the previous bar
        for f in fills:
            self.pf.apply(f, self.spec)
            self.events.append(Event(ts, "fill", {"qty": f.qty, "price": f.price,
                                                  "cost": f.cost}))
        self.bars.append(bar)
        day = pd.Timestamp(ts).date()
        acct = self._account(bar["close"])
        if day != self.day:
            self.day, self.day_start = day, acct.equity
            acct.day_start_equity = acct.equity
        was = self.risk.kill.engaged
        self.risk.monitor(acct, ts)
        if self.risk.kill.engaged and not was:
            self.events.append(Event(ts, "kill_switch", {"reason": self.risk.kill.reason}))
        df = pd.DataFrame(self.bars[-self.history:])
        target = float(self.strategy.target_position(df, self.params).iloc[-1])
        if self.risk.kill.engaged:
            target = 0.0  # flatten; the risk service only lets reducing orders through
        atr = ind.atr(df, 14).iloc[-1]
        stop_dist = self.stop_atr * (atr if atr == atr else bar["close"] * 0.02)
        want = self._size(target, bar["close"], stop_dist, acct.equity)
        have = self.pf.units.get(sym, 0.0)
        qty = want - have
        if abs(qty) < self.spec.lot_size / 2:
            return fills
        stop = bar["close"] - math.copysign(stop_dist, want) if want else None
        coid = client_order_id(self.strategy.meta.key, sym, ts)
        intent = OrderIntent(coid, sym, qty, bar["close"], self.spec.contract_multiplier, stop)
        decision = self.risk.check(intent, acct, data_stale=data_stale, now=ts)
        if decision:
            self.broker.submit(Order(coid, sym, qty, stop))
        else:
            self.events.append(Event(ts, "refused", {"qty": qty,
                                                     "reasons": list(decision.reasons)}))
        return fills
