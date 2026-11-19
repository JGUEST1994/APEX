"""Pre-trade risk checks and the kill switch.

The risk service sees only order intents and account/market state. It does not know which
strategy produced an order and cannot be configured from strategy code. Any refusal is
final for that order; the order manager never retries a refused intent with a larger size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from apex.core.config import TradingMode
from apex.risk.limits import RiskLimits


@dataclass(frozen=True)
class OrderIntent:
    client_order_id: str  # deterministic from (strategy, symbol, bar ts) -> idempotent
    symbol: str
    qty: float  # signed units to trade
    price: float  # reference price (last close)
    multiplier: float = 1.0
    stop_price: float | None = None


@dataclass
class AccountState:
    equity: float
    day_start_equity: float
    peak_equity: float
    positions: dict[str, float] = field(default_factory=dict)  # symbol -> signed notional
    consecutive_losses: int = 0
    clusters: dict[str, str] = field(default_factory=dict)  # symbol -> correlated group


@dataclass
class KillSwitch:
    engaged: bool = False
    reason: str = ""
    at: datetime | None = None
    by: str = ""

    def engage(self, reason: str, at: datetime, by: str = "risk") -> None:
        if not self.engaged:
            self.engaged, self.reason, self.at, self.by = True, reason, at, by

    def reset(self, actor: str, at: datetime) -> None:
        """Manual only: the risk service itself can never reset it."""
        if actor in ("", "system", "risk"):
            raise PermissionError("the kill switch can only be reset by a named human")
        self.engaged, self.reason, self.at, self.by = False, f"reset by {actor}", at, actor


@dataclass(frozen=True)
class Decision:
    approved: bool
    reasons: tuple[str, ...]

    def __bool__(self) -> bool:
        return self.approved


class RiskService:
    def __init__(self, limits: RiskLimits, mode: TradingMode,
                 kill_switch: KillSwitch | None = None) -> None:
        if mode is not TradingMode.PAPER:
            raise ValueError("the risk service only runs in paper mode")
        self.limits = limits
        self.kill = kill_switch or KillSwitch()
        self.seen: set[str] = set()

    def tighten(self, **changes: float) -> None:
        self.limits = self.limits.tightened(**changes)

    def monitor(self, acct: AccountState, now: datetime) -> None:
        """Account-level checks run every cycle; breaches engage the kill switch."""
        lim = self.limits
        if acct.equity <= 0:
            self.kill.engage("equity <= 0", now)
        elif acct.peak_equity > 0 and 1 - acct.equity / acct.peak_equity >= lim.max_drawdown:
            self.kill.engage(f"drawdown >= {lim.max_drawdown:.1%}", now)
        elif acct.day_start_equity > 0 and \
                1 - acct.equity / acct.day_start_equity >= lim.max_daily_loss:
            self.kill.engage(f"daily loss >= {lim.max_daily_loss:.1%}", now)
        elif acct.consecutive_losses >= lim.max_consecutive_losses:
            # Jake's decision (2026-11-19): a losing streak stops the account until a named
            # human resets the kill switch; the reset also clears the streak.
            self.kill.engage(f"{acct.consecutive_losses} losing trades in a row", now)

    def check(self, o: OrderIntent, acct: AccountState, *, data_stale: bool,
              now: datetime) -> Decision:
        self.monitor(acct, now)
        lim, r = self.limits, []
        if o.client_order_id in self.seen:
            return Decision(False, ("duplicate client_order_id",))
        self.seen.add(o.client_order_id)
        cur = acct.positions.get(o.symbol, 0.0)
        new = cur + o.qty * o.price * o.multiplier
        reducing = abs(new) < abs(cur) and (new == 0 or (new > 0) == (cur > 0))
        if self.kill.engaged and not reducing:
            r.append(f"kill switch engaged: {self.kill.reason}")
        if data_stale and not reducing:
            r.append("market data is stale")
        if o.qty == 0 or o.price <= 0:
            r.append("invalid quantity or price")
        eq = acct.equity
        if not reducing and eq > 0:
            others = {k: v for k, v in acct.positions.items() if k != o.symbol and v != 0}
            gross = sum(abs(v) for v in others.values()) + abs(new)
            net = abs(sum(others.values()) + new)
            if abs(new) / eq > lim.max_position_notional:
                r.append("max position size")
            if gross / eq > lim.max_gross_exposure:
                r.append("max gross exposure")
            if net / eq > lim.max_net_exposure:
                r.append("max net exposure")
            if gross / eq > lim.max_leverage:
                r.append("max leverage")
            group = acct.clusters.get(o.symbol, o.symbol)
            in_group = sum(abs(v) for k, v in others.items()
                           if acct.clusters.get(k, k) == group) + abs(new)
            if in_group / eq > lim.max_cluster_exposure:
                r.append("max correlated exposure")
            if cur == 0 and len(others) + 1 > lim.max_positions:
                r.append("max concurrent positions")
            if acct.consecutive_losses >= lim.max_consecutive_losses:
                r.append("consecutive losses limit")
            if o.stop_price is None:
                r.append("stop price required for new risk")
            else:
                risk = abs(o.qty) * abs(o.price - o.stop_price) * o.multiplier
                if risk / eq > lim.max_risk_per_trade:
                    r.append("max risk per trade")
        return Decision(not r, tuple(r))
