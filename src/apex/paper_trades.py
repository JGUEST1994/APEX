"""Practice (paper) trades as round trips: each finished trade with its profit or loss, the
trades still open with their profit or loss so far, and a win/loss summary.

Read-only report rebuilt from the fill ledger; it changes nothing in the paper trading code
or its records. A trade starts when a market's position moves away from flat and ends when
it is back to flat or reverses side (the remainder of a reversing fill starts the next trade).
Top-ups and partial sells in between belong to the same trade.

Profit or loss uses exactly the paper portfolio's arithmetic (``apex.execution.paper``):
closed units x (fill price - average cost) x contract multiplier, minus every fill's costs,
with a reversing fill's whole cost charged to the trade it closes. So the losing streak
shown here is the one the kill switch counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.db.models import PaperAccount, PaperFill, PriceBar
from apex.marketdata.repository import get_instrument, to_spec


@dataclass
class _Open:
    side: int  # +1 long, -1 short
    opened: datetime
    pnl: float = 0.0  # realised so far, net of costs
    in_qty: float = 0.0
    in_value: float = 0.0
    out_qty: float = 0.0
    out_value: float = 0.0
    peak_units: float = 0.0
    fills: int = 0
    last: datetime | None = None

    def add_in(self, qty: float, price: float) -> None:
        self.in_qty += abs(qty)
        self.in_value += abs(qty) * price

    def add_out(self, qty: float, price: float) -> None:
        self.out_qty += abs(qty)
        self.out_value += abs(qty) * price


def _row(sym: str, t: _Open, mult: float) -> dict:
    entry = t.in_value / t.in_qty if t.in_qty else None
    exit_ = t.out_value / t.out_qty if t.out_qty else None
    stake = (entry or 0.0) * t.peak_units * mult
    return {"symbol": sym, "side": "long" if t.side > 0 else "short",
            "opened": t.opened.isoformat(), "units": t.peak_units,
            "entry_price": entry, "exit_price": exit_, "fills": t.fills,
            "pnl": t.pnl, "return": t.pnl / stake if stake else None}


def _summary(closed: list[dict]) -> dict:
    wins = [t["pnl"] for t in closed if t["pnl"] > 0]
    losses = [t["pnl"] for t in closed if t["pnl"] < 0]
    streak_kind, streak = None, 0
    for t in closed:  # newest first
        kind = "win" if t["pnl"] > 0 else "loss" if t["pnl"] < 0 else "even"
        if streak_kind is None:
            streak_kind = kind
        if kind != streak_kind:
            break
        streak += 1
    return {
        "trades": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(closed) if closed else None,
        "total_pnl": sum(t["pnl"] for t in closed),
        "average_win": sum(wins) / len(wins) if wins else None,
        "average_loss": sum(losses) / len(losses) if losses else None,
        "best": max((t["pnl"] for t in closed), default=None),
        "worst": min((t["pnl"] for t in closed), default=None),
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "average_days_held": (sum(t["days_held"] for t in closed) / len(closed)
                              if closed else None),
        "streak": {"kind": streak_kind, "count": streak},
    }


def trades(session: Session, name: str, limit: int = 50) -> dict:
    """Finished trades (newest first, at most ``limit``), open trades and a summary of all
    finished trades."""
    acc = session.scalar(select(PaperAccount).where(PaperAccount.name == name))
    if acc is None:
        raise KeyError(name)
    fills = session.scalars(select(PaperFill).where(PaperFill.account_id == acc.id)
                            .order_by(PaperFill.id)).all()
    mults: dict[str, float] = {}
    units: dict[str, float] = {}
    avg: dict[str, float] = {}
    live: dict[str, _Open] = {}
    closed: list[dict] = []

    def mult(sym: str) -> float:
        if sym not in mults:
            mults[sym] = to_spec(get_instrument(session, sym)).contract_multiplier
        return mults[sym]

    for f in fills:
        m, cur = mult(f.symbol), units.get(f.symbol, 0.0)
        new = cur + f.qty
        t = live.get(f.symbol)
        if t is None:  # opening from flat
            t = live[f.symbol] = _Open(side=1 if f.qty > 0 else -1, opened=f.ts)
        t.pnl -= f.cost
        t.fills += 1
        t.last = f.ts
        if cur != 0 and (cur > 0) != (f.qty > 0):  # closing (part of) a position
            closed_units = min(abs(f.qty), abs(cur)) * (1 if cur > 0 else -1)
            t.pnl += closed_units * (f.price - avg[f.symbol]) * m
            t.add_out(closed_units, f.price)
            if new == 0 or (new > 0) != (cur > 0):  # round trip finished
                row = _row(f.symbol, t, m)
                row["closed"] = f.ts.isoformat()
                row["days_held"] = (f.ts - t.opened).total_seconds() / 86400
                row["result"] = "win" if t.pnl > 0 else "loss" if t.pnl < 0 else "even"
                row["exit_order"] = f.client_order_id
                closed.append(row)
                del live[f.symbol]
                if new != 0:  # reversed: the rest starts a new trade, costs already charged
                    t = live[f.symbol] = _Open(side=1 if new > 0 else -1, opened=f.ts,
                                               fills=1, last=f.ts)
                    t.add_in(new, f.price)
        else:
            t.add_in(f.qty, f.price)
        if new == 0:
            avg.pop(f.symbol, None)
        elif cur == 0 or (cur > 0) != (new > 0):
            avg[f.symbol] = f.price
        elif abs(new) > abs(cur):
            avg[f.symbol] = (avg[f.symbol] * cur + f.price * f.qty) / new
        units[f.symbol] = new
        if f.symbol in live:
            live[f.symbol].peak_units = max(live[f.symbol].peak_units, abs(new))

    open_rows = []
    for sym, t in live.items():
        row = _row(sym, t, mult(sym))
        last = _last_close(session, acc, sym)
        row["units"] = abs(units[sym])
        row["last_price"] = last
        row["unrealised"] = units[sym] * (last - avg[sym]) * mult(sym) if last else None
        row["pnl"] = t.pnl + (row["unrealised"] or 0.0)
        stake = (row["entry_price"] or 0.0) * t.peak_units * mult(sym)
        row["return"] = row["pnl"] / stake if stake else None
        row["days_held"] = ((acc.last_bar_ts - t.opened).total_seconds() / 86400
                            if acc.last_bar_ts else None)
        open_rows.append(row)

    _exit_reasons(acc, closed)
    for row in closed:
        del row["exit_order"]
    closed.reverse()  # newest first
    return {"account": name, "simulated": True,
            "summary": _summary(closed),
            "open": sorted(open_rows, key=lambda r: r["opened"], reverse=True),
            "open_pnl": sum(r["pnl"] for r in open_rows),
            "closed": closed[:limit], "closed_total": len(closed)}


EXIT_REASONS = {
    "safety": "Safety stop",
    "flip": "Flipped side",
    "signal": "Strategy got out",
    "resize": "Sized down to zero",
}


def _exit_reasons(acc: PaperAccount, closed: list[dict]) -> None:
    """Why each finished trade ended, worked out from the records (strategies give target
    positions, not reasons): an exit filled after the kill switch engaged is a safety stop;
    a trade followed at once by one on the other side of the same market is a flip; an exit
    order (the trader's always-allowed ``-x`` closing order) is the strategy getting out;
    anything else reached flat through a resize. Only the current kill-switch engagement is
    stored, so exits under an earlier one (since reset) count as strategy exits."""
    opened = {(r["symbol"], r["opened"], r["side"]) for r in closed}
    for r in closed:
        other = "short" if r["side"] == "long" else "long"
        if acc.kill_engaged and acc.kill_at and r["closed"] > acc.kill_at.isoformat():
            key = "safety"
        elif (r["symbol"], r["closed"], other) in opened:
            key = "flip"
        elif r["exit_order"].endswith("-x"):
            key = "signal"
        else:
            key = "resize"
        r["exit_reason"] = key
        r["exit_reason_label"] = EXIT_REASONS[key]


def _last_close(session: Session, acc: PaperAccount, sym: str) -> float | None:
    """Close of the newest bar the account has processed for ``sym``."""
    if acc.last_bar_ts is None:
        return None
    inst = get_instrument(session, sym)
    return session.scalar(
        select(PriceBar.close).where(PriceBar.instrument_id == inst.id,
                                     PriceBar.timeframe == acc.timeframe,
                                     PriceBar.source == acc.source,
                                     PriceBar.ts <= acc.last_bar_ts)
        .order_by(PriceBar.ts.desc()).limit(1))
