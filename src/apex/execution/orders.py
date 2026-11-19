"""Order state machine and deterministic client order ids."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OrderStatus(str, Enum):
    NEW = "new"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


S = OrderStatus
_ALLOWED = {
    S.NEW: {S.SUBMITTED, S.REJECTED, S.CANCELLED},
    S.SUBMITTED: {S.ACKNOWLEDGED, S.REJECTED, S.CANCELLED, S.EXPIRED},
    S.ACKNOWLEDGED: {S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED, S.EXPIRED},
    S.PARTIALLY_FILLED: {S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED, S.EXPIRED},
}
TERMINAL = {S.FILLED, S.CANCELLED, S.REJECTED, S.EXPIRED}


class IllegalOrderTransition(ValueError):
    pass


def client_order_id(strategy_key: str, symbol: str, signal_ts: datetime,
                    account: str = "") -> str:
    """Same signal in the same account -> same id, so a restart can never submit it twice;
    different accounts never collide."""
    raw = f"{account}|{strategy_key}|{symbol}|{signal_ts.isoformat()}"
    return "apx-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass
class Order:
    client_order_id: str
    symbol: str
    qty: float  # signed
    stop_price: float | None = None
    status: OrderStatus = OrderStatus.NEW
    filled_qty: float = 0.0
    avg_price: float = 0.0
    reason: str = ""
    history: list[tuple[str, str]] = field(default_factory=list)

    def to(self, status: OrderStatus, reason: str = "") -> None:
        if status not in _ALLOWED.get(self.status, set()):
            raise IllegalOrderTransition(f"{self.status.value} -> {status.value}")
        self.history.append((self.status.value, status.value))
        self.status, self.reason = status, reason or self.reason

    def fill(self, qty: float, price: float) -> None:
        if self.status not in (S.ACKNOWLEDGED, S.PARTIALLY_FILLED):
            raise IllegalOrderTransition(f"cannot fill an order in state {self.status.value}")
        if abs(self.filled_qty + qty) > abs(self.qty) + 1e-12 or qty * self.qty < 0:
            raise IllegalOrderTransition("overfill or wrong-side fill")
        total = self.filled_qty + qty
        self.avg_price = (self.avg_price * self.filled_qty + price * qty) / total
        self.filled_qty = total
        done = abs(total - self.qty) <= 1e-12
        self.to(S.FILLED if done else S.PARTIALLY_FILLED)
