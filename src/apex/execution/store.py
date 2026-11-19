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

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.core.config import TradingMode
from apex.db.models import AuditEvent, PaperAccount, PaperFill, PaperOrder, PaperPosition
from apex.domain.market import Timeframe
from apex.execution.orders import TERMINAL, Order, OrderStatus
from apex.execution.paper import Fill, Portfolio
from apex.execution.trader import PaperTrader
from apex.marketdata.repository import get_instrument, load_bars, to_spec
from apex.risk.limits import RiskLimits  # noqa: F401  (re-exported for callers)
from apex.risk.service import KillSwitch, RiskService
from apex.strategies.registry import all_strategies

TOL = 1e-6


class AccountHalted(RuntimeError):
    pass


def create_account(session: Session, name: str, *, strategy_key: str, symbol: str | list[str],
                   timeframe: str, source: str, capital: float = 100_000.0,
                   params: dict | None = None, limits: RiskLimits | None = None,
                   actor: str = "system") -> PaperAccount:
    if strategy_key not in all_strategies():
        raise KeyError(f"unknown strategy {strategy_key}")
    all_strategies()[strategy_key]().params(params)  # validates parameter names
    symbols = [symbol] if isinstance(symbol, str) else list(dict.fromkeys(symbol))
    if not symbols:
        raise ValueError("at least one symbol is required")
    for sym in symbols:
        get_instrument(session, sym)
    acc = PaperAccount(name=name, strategy_key=strategy_key, params=params or {},
                       symbol=symbols[0], symbols=symbols if len(symbols) > 1 else None,
                       timeframe=timeframe, source=source,
                       limits=asdict(limits or RiskLimits()), initial_capital=capital,
                       cash=capital, peak_equity=capital, day_start_equity=capital)
    session.add(acc)
    session.add(AuditEvent(actor=actor, action="paper_account.create", target=name,
                           detail={"strategy": strategy_key, "symbols": symbols}))
    session.flush()
    return acc


def _specs(session: Session, acc: PaperAccount) -> dict:
    return {s: to_spec(get_instrument(session, s)) for s in acc.all_symbols}


def reconcile(session: Session, acc: PaperAccount) -> list[str]:
    """Compare stored positions/cash with the fill ledger. Returns the problems found."""
    specs = _specs(session, acc)
    fills = session.scalars(select(PaperFill).where(PaperFill.account_id == acc.id)).all()
    units: dict[str, float] = {}
    cash = acc.initial_capital
    for f in fills:
        units[f.symbol] = units.get(f.symbol, 0.0) + f.qty
        cash -= f.qty * f.price * specs[f.symbol].contract_multiplier + f.cost
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
    specs = _specs(session, acc)
    kill = KillSwitch(acc.kill_engaged, acc.kill_reason, acc.kill_at, acc.kill_by)
    risk = RiskService(RiskLimits(**acc.limits), TradingMode.PAPER, kill)
    t = PaperTrader(all_strategies()[acc.strategy_key](), list(specs.values()), risk,
                    initial_capital=acc.initial_capital, params=acc.params,
                    account=acc.name)
    t.pf.cash, t.pf.realised = acc.cash, acc.realised
    t.pf.consecutive_losses = acc.consecutive_losses
    for p in session.scalars(select(PaperPosition).where(PaperPosition.account_id == acc.id)):
        if p.units:
            t.pf.units[p.symbol], t.pf.avg_cost[p.symbol] = p.units, p.avg_cost
    replay = Portfolio(cash=acc.initial_capital)  # open round-trip P&L for the loss streak
    for f in session.scalars(select(PaperFill).where(PaperFill.account_id == acc.id)
                             .order_by(PaperFill.id)):
        replay.apply(Fill(f.client_order_id, f.symbol, f.qty, f.price, f.cost),
                     specs[f.symbol])
    t.pf.trade_pnl = replay.trade_pnl
    t.peak, t.day_start = acc.peak_equity, acc.day_start_equity
    t.day = date.fromisoformat(acc.day) if acc.day else None
    for o in session.scalars(select(PaperOrder).where(PaperOrder.account_id == acc.id)
                             .order_by(PaperOrder.id)):  # closes before re-entries
        risk.seen.add(o.client_order_id)
        if OrderStatus(o.status) not in TERMINAL:
            order = Order(o.client_order_id, o.symbol, o.qty, o.stop_price,
                          OrderStatus(o.status), o.filled_qty, o.avg_price, o.reason)
            t.broker.orders[o.client_order_id] = t.broker.working[o.client_order_id] = order
    if acc.last_bar_ts is not None:  # warm-up history for the strategy
        for sym in specs:
            hist = load_bars(session, get_instrument(session, sym).id,
                             Timeframe(acc.timeframe), acc.source, None, None)
            hist = hist[hist["ts"] <= acc.last_bar_ts].tail(t.history)
            t.hist[sym] = hist.to_dict("records")
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
    start = acc.last_bar_ts
    frames = []
    for sym in acc.all_symbols:
        b = load_bars(session, get_instrument(session, sym).id, Timeframe(acc.timeframe),
                      acc.source, start, end)
        frames.append(b.assign(symbol=sym))
    bars = pd.concat(frames, ignore_index=True)
    if start is not None:
        bars = bars[bars["ts"] > start]
    n = 0
    for ts, group in bars.sort_values(["ts", "symbol"]).groupby("ts", sort=True):
        if max_bars is not None and n >= max_bars:
            break
        batch = {r["symbol"]: {k: v for k, v in r.items() if k != "symbol"}
                 for r in group.to_dict("records")}
        fills = t.on_bars(batch)
        _save_bar(session, acc, t, fills, ts)
        session.commit()
        n += 1
    prices = t.last_prices()
    equity = t.pf.equity(prices, {s: t.specs[s] for s in prices}) if prices else acc.cash
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
    streak, acc.consecutive_losses = acc.consecutive_losses, 0  # a human chose to restart
    session.add(AuditEvent(actor=actor, action="kill_switch.reset", target=name,
                           detail={"cleared_losing_streak": streak}))
