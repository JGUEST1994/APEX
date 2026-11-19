"""Persistence, restart and reconciliation for paper accounts (Phase 9 slice 2).

Each processed bar is committed in ONE transaction: fills, orders, positions, cash, kill
switch and ``last_bar_ts`` move together, so a crash can at worst lose the bar in flight,
which is then re-processed on restart. Client order ids are deterministic, so a replayed
bar cannot create a second order. On load, positions and cash are rebuilt from the fill
ledger and compared with the stored state; any mismatch halts the account.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.core.config import TradingMode
from apex.db.models import AuditEvent, PaperAccount, PaperFill, PaperOrder, PaperPosition
from apex.domain.market import Timeframe
from apex.execution.orders import TERMINAL, Order, OrderStatus
from apex.execution.trader import PaperTrader
from apex.marketdata.repository import get_instrument, load_bars, to_spec
from apex.risk.limits import RiskLimits  # noqa: F401  (re-exported for callers)
from apex.risk.service import KillSwitch, RiskService
from apex.strategies.registry import all_strategies

TOL = 1e-6


class AccountHalted(RuntimeError):
    pass


def create_account(session: Session, name: str, *, strategy_key: str, symbol: str,
                   timeframe: str, source: str, capital: float = 100_000.0,
                   params: dict | None = None, limits: RiskLimits | None = None,
                   actor: str = "system") -> PaperAccount:
    if strategy_key not in all_strategies():
        raise KeyError(f"unknown strategy {strategy_key}")
    all_strategies()[strategy_key]().params(params)  # validates parameter names
    get_instrument(session, symbol)
    acc = PaperAccount(name=name, strategy_key=strategy_key, params=params or {},
                       symbol=symbol, timeframe=timeframe, source=source,
                       limits=asdict(limits or RiskLimits()), initial_capital=capital,
                       cash=capital, peak_equity=capital, day_start_equity=capital)
    session.add(acc)
    session.add(AuditEvent(actor=actor, action="paper_account.create", target=name,
                           detail={"strategy": strategy_key, "symbol": symbol}))
    session.flush()
    return acc


def reconcile(session: Session, acc: PaperAccount) -> list[str]:
    """Compare stored positions/cash with the fill ledger. Returns the problems found."""
    spec = to_spec(get_instrument(session, acc.symbol))
    fills = session.scalars(select(PaperFill).where(PaperFill.account_id == acc.id)).all()
    units: dict[str, float] = {}
    cash = acc.initial_capital
    for f in fills:
        units[f.symbol] = units.get(f.symbol, 0.0) + f.qty
        cash -= f.qty * f.price * spec.contract_multiplier + f.cost
    stored = {p.symbol: p.units for p in session.scalars(
        select(PaperPosition).where(PaperPosition.account_id == acc.id))}
    problems = [f"{s}: ledger {units.get(s, 0.0)} != stored {stored.get(s, 0.0)}"
                for s in set(units) | set(stored)
                if abs(units.get(s, 0.0) - stored.get(s, 0.0)) > TOL]
    if abs(cash - acc.cash) > 1e-4 * max(1.0, abs(acc.initial_capital)):
        problems.append(f"cash: ledger {cash:.2f} != stored {acc.cash:.2f}")
    for o in session.scalars(select(PaperOrder).where(PaperOrder.account_id == acc.id)):
        filled = sum(f.qty for f in fills if f.client_order_id == o.client_order_id)
        if abs(filled - o.filled_qty) > TOL:
            problems.append(f"order {o.client_order_id}: fills {filled} != {o.filled_qty}")
    return problems


def _halt(session: Session, acc: PaperAccount, reason: str, now: datetime) -> None:
    acc.status = "halted"
    if not acc.kill_engaged:
        acc.kill_engaged, acc.kill_reason, acc.kill_at, acc.kill_by = True, reason, now, "risk"
    session.add(AuditEvent(actor="risk", action="paper_account.halt", target=acc.name,
                           detail={"reason": reason}))


def load_trader(session: Session, acc: PaperAccount, now: datetime) -> PaperTrader:
    """Rebuild a trader from stored state. Halts (and raises) if reconciliation fails."""
    if acc.status == "halted":
        raise AccountHalted(f"{acc.name} is halted: {acc.kill_reason}")
    problems = reconcile(session, acc)
    if problems:
        _halt(session, acc, "reconciliation mismatch: " + "; ".join(problems), now)
        raise AccountHalted("; ".join(problems))
    spec = to_spec(get_instrument(session, acc.symbol))
    kill = KillSwitch(acc.kill_engaged, acc.kill_reason, acc.kill_at, acc.kill_by)
    risk = RiskService(RiskLimits(**acc.limits), TradingMode.PAPER, kill)
    t = PaperTrader(all_strategies()[acc.strategy_key](), spec, risk,
                    initial_capital=acc.initial_capital, params=acc.params,
                    account=acc.name)
    t.pf.cash, t.pf.realised = acc.cash, acc.realised
    t.pf.consecutive_losses = acc.consecutive_losses
    for p in session.scalars(select(PaperPosition).where(PaperPosition.account_id == acc.id)):
        if p.units:
            t.pf.units[p.symbol], t.pf.avg_cost[p.symbol] = p.units, p.avg_cost
    t.peak, t.day_start = acc.peak_equity, acc.day_start_equity
    t.day = date.fromisoformat(acc.day) if acc.day else None
    for o in session.scalars(select(PaperOrder).where(PaperOrder.account_id == acc.id)):
        risk.seen.add(o.client_order_id)
        if OrderStatus(o.status) not in TERMINAL:
            order = Order(o.client_order_id, o.symbol, o.qty, o.stop_price,
                          OrderStatus(o.status), o.filled_qty, o.avg_price, o.reason)
            t.broker.orders[o.client_order_id] = t.broker.working[o.client_order_id] = order
    if acc.last_bar_ts is not None:  # warm-up history for the strategy
        hist = load_bars(session, get_instrument(session, acc.symbol).id,
                         Timeframe(acc.timeframe), acc.source, None, None)
        hist = hist[hist["ts"] <= acc.last_bar_ts].tail(t.history)
        t.bars = hist.to_dict("records")
    return t


def _save_bar(session: Session, acc: PaperAccount, t: PaperTrader, fills, ts) -> None:  # noqa: ANN001
    for f in fills:
        session.add(PaperFill(account_id=acc.id, client_order_id=f.client_order_id,
                              symbol=f.symbol, qty=f.qty, price=f.price, cost=f.cost, ts=ts))
    existing = {o.client_order_id: o for o in session.scalars(
        select(PaperOrder).where(PaperOrder.account_id == acc.id))}
    for coid, o in t.broker.orders.items():
        row = existing.get(coid)
        if row is None:
            session.add(PaperOrder(account_id=acc.id, client_order_id=coid, symbol=o.symbol,
                                   qty=o.qty, stop_price=o.stop_price, status=o.status.value,
                                   filled_qty=o.filled_qty, avg_price=o.avg_price,
                                   reason=o.reason, signal_ts=ts))
        elif row.status != o.status.value or row.filled_qty != o.filled_qty:
            row.status, row.filled_qty, row.avg_price = (o.status.value, o.filled_qty,
                                                         o.avg_price)
    positions = {p.symbol: p for p in session.scalars(
        select(PaperPosition).where(PaperPosition.account_id == acc.id))}
    for sym, units in t.pf.units.items():
        p = positions.get(sym)
        if p is None:
            session.add(PaperPosition(account_id=acc.id, symbol=sym, units=units,
                                      avg_cost=t.pf.avg_cost.get(sym, 0.0)))
        else:
            p.units, p.avg_cost = units, t.pf.avg_cost.get(sym, 0.0)
    k = t.risk.kill
    if k.engaged and not acc.kill_engaged:
        session.add(AuditEvent(actor=k.by or "risk", action="kill_switch.engage",
                               target=acc.name, detail={"reason": k.reason}))
    acc.cash, acc.realised = t.pf.cash, t.pf.realised
    acc.consecutive_losses = t.pf.consecutive_losses
    acc.peak_equity, acc.day_start_equity = t.peak, t.day_start
    acc.day = t.day.isoformat() if t.day else None
    acc.kill_engaged, acc.kill_reason, acc.kill_at, acc.kill_by = (k.engaged, k.reason, k.at,
                                                                   k.by)
    acc.last_bar_ts = ts


def run_account(session: Session, name: str, *, now: datetime,
                end: datetime | None = None, max_bars: int | None = None) -> dict:
    """Feed every stored bar after ``last_bar_ts`` (up to ``end``) through the account,
    committing after each bar. Returns a short summary."""
    acc = session.scalar(select(PaperAccount).where(PaperAccount.name == name))
    if acc is None:
        raise KeyError(name)
    try:
        t = load_trader(session, acc, now)
    except AccountHalted:
        session.commit()
        raise
    inst = get_instrument(session, acc.symbol)
    start = acc.last_bar_ts
    bars = load_bars(session, inst.id, Timeframe(acc.timeframe), acc.source, start, end)
    if start is not None:
        bars = bars[bars["ts"] > start]
    n = 0
    for bar in bars.to_dict("records"):
        if max_bars is not None and n >= max_bars:
            break
        fills = t.on_bar(bar)
        _save_bar(session, acc, t, fills, bar["ts"])
        session.commit()
        n += 1
    price = t.bars[-1]["close"] if t.bars else 0.0
    equity = t.pf.equity({acc.symbol: price}, {acc.symbol: t.spec}) if t.bars else acc.cash
    return {"account": name, "bars": n, "equity": equity, "units": dict(t.pf.units),
            "kill_switch": acc.kill_engaged, "kill_reason": acc.kill_reason,
            "last_bar_ts": acc.last_bar_ts, "simulated": True}


def reset_kill_switch(session: Session, name: str, actor: str, now: datetime) -> None:
    """Human-only reset. Also clears a halt, after reconciliation passes again."""
    acc = session.scalar(select(PaperAccount).where(PaperAccount.name == name))
    if acc is None:
        raise KeyError(name)
    KillSwitch(acc.kill_engaged, acc.kill_reason, acc.kill_at, acc.kill_by).reset(actor, now)
    problems = reconcile(session, acc)
    if problems:
        raise AccountHalted("cannot reset: " + "; ".join(problems))
    acc.kill_engaged, acc.kill_reason, acc.kill_at, acc.kill_by = (False, f"reset by {actor}",
                                                                   now, actor)
    acc.status = "running"
    session.add(AuditEvent(actor=actor, action="kill_switch.reset", target=name, detail={}))
