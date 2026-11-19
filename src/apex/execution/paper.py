"""Paper broker and portfolio: simulated fills only, same cost model as the backtester.

A market order accepted on bar t fills at the OPEN of the next bar it sees (latency >= 1
bar), exactly like the event-driven backtester. There is no code path to a real broker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apex.backtest.engine import fill_cost
from apex.domain.market import InstrumentSpec
from apex.execution.orders import Order, OrderStatus


@dataclass
class Fill:
    client_order_id: str
    symbol: str
    qty: float
    price: float
    cost: float


@dataclass
class Portfolio:
    cash: float
    units: dict[str, float] = field(default_factory=dict)
    avg_cost: dict[str, float] = field(default_factory=dict)
    realised: float = 0.0
    consecutive_losses: int = 0

    def apply(self, f: Fill, spec: InstrumentSpec) -> None:
        m = spec.contract_multiplier
        cur = self.units.get(f.symbol, 0.0)
        new = cur + f.qty
        if cur != 0 and (cur > 0) != (f.qty > 0):  # closing (part of) a position
            closed = min(abs(f.qty), abs(cur)) * (1 if cur > 0 else -1)
            pnl = closed * (f.price - self.avg_cost[f.symbol]) * m
            self.realised += pnl
            self.consecutive_losses = self.consecutive_losses + 1 if pnl - f.cost < 0 else 0
        if new == 0:
            self.avg_cost.pop(f.symbol, None)
        elif cur == 0 or (cur > 0) != (new > 0):
            self.avg_cost[f.symbol] = f.price
        elif abs(new) > abs(cur):
            self.avg_cost[f.symbol] = (self.avg_cost[f.symbol] * cur + f.price * f.qty) / new
        self.units[f.symbol] = new
        self.cash -= f.qty * f.price * m + f.cost

    def equity(self, prices: dict[str, float], specs: dict[str, InstrumentSpec]) -> float:
        return self.cash + sum(u * prices[s] * specs[s].contract_multiplier
                               for s, u in self.units.items() if u)

    def notionals(self, prices: dict[str, float],
                  specs: dict[str, InstrumentSpec]) -> dict[str, float]:
        return {s: u * prices[s] * specs[s].contract_multiplier
                for s, u in self.units.items() if u}


class PaperBroker:
    simulated = True

    def __init__(self, specs: dict[str, InstrumentSpec], cost_multiplier: float = 1.0) -> None:
        self.specs = specs
        self.cost_multiplier = cost_multiplier
        self.working: dict[str, Order] = {}
        self.orders: dict[str, Order] = {}

    def submit(self, order: Order) -> Order:
        if order.client_order_id in self.orders:  # idempotent: return the existing order
            return self.orders[order.client_order_id]
        order.to(OrderStatus.SUBMITTED)
        order.to(OrderStatus.ACKNOWLEDGED)
        self.orders[order.client_order_id] = order
        self.working[order.client_order_id] = order
        return order

    def cancel(self, coid: str) -> None:
        o = self.working.pop(coid, None)
        if o:
            o.to(OrderStatus.CANCELLED, "cancelled")

    def on_bar_open(self, symbol: str, open_price: float) -> list[Fill]:
        fills = []
        for coid, o in list(self.working.items()):
            if o.symbol != symbol:
                continue
            spec = self.specs[symbol]
            qty = o.qty - o.filled_qty
            cost = fill_cost(spec, qty, open_price, self.cost_multiplier)
            o.fill(qty, open_price)
            fills.append(Fill(coid, symbol, qty, open_price, cost))
            del self.working[coid]
        return fills
