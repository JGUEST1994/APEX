"""Paper-vs-backtest consistency report (Phase 9).

Re-runs the backtester over exactly the bars a paper account has processed, with the same
strategy and parameters, and compares the two. Paper sizes are risk-capped and resized
every bar, so the comparison is on *direction* (long / flat / short per bar), entry/exit
timing and fill prices rather than on unit counts. A strategy should not be approved by a
human while its paper record disagrees with its backtest.

An account created with ``start_now`` (or a comparison given ``since``) is compared only on
the bars after its start. The backtest still runs over all stored history, so the strategy
is warmed up the same way; the first bar after the start is left out, because a new account
takes up a position the strategy already holds one bar later than the backtest did.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.backtest.engine import run_backtest
from apex.backtest.portfolio import run_portfolio_backtest
from apex.db.models import PaperAccount, PaperFill
from apex.domain.market import Timeframe
from apex.marketdata.repository import get_instrument, load_bars, to_spec
from apex.strategies.multi import align, all_multi_strategies
from apex.strategies.registry import all_strategies

MIN_AGREEMENT = 0.95  # share of bars where paper and backtest hold the same direction
MAX_PRICE_GAP_BPS = 1.0  # paper and backtest both fill at the bar open; should be ~equal


def _changes(direction: pd.Series) -> set:
    d = direction.to_numpy()
    idx = np.flatnonzero(np.diff(d, prepend=0) != 0)
    return {direction.index[i] for i in idx}


def _bars(session: Session, acc: PaperAccount, sym: str) -> pd.DataFrame:
    inst = get_instrument(session, sym)
    bars = load_bars(session, inst.id, Timeframe(acc.timeframe), acc.source, None, None)
    return bars[bars["ts"] <= acc.last_bar_ts].reset_index(drop=True)


def _window_return(equity, ts: pd.DatetimeIndex, since: datetime | None) -> float:  # noqa: ANN001
    eq = pd.Series(np.asarray(equity, float), index=ts)
    if since is not None and (eq.index <= pd.Timestamp(since)).any():
        eq = eq[eq.index >= eq.index[eq.index <= pd.Timestamp(since)][-1]]
    return float(eq.iloc[-1] / eq.iloc[0] - 1)


def _one(session: Session, acc: PaperAccount, sym: str, fills: list[PaperFill],
         since: datetime | None = None) -> dict:
    bars = _bars(session, acc, sym)
    spec = to_spec(get_instrument(session, sym))
    bt = run_backtest(bars, all_strategies()[acc.strategy_key](), spec, acc.params)
    ts = pd.DatetimeIndex(bars["ts"])
    return _versus(bars, fills, pd.Series(bt.position.to_numpy(), index=bars["ts"]),
                   _window_return(bt.equity, ts, since), since)


def _multi(session: Session, acc: PaperAccount, fills: list[PaperFill],
           since: datetime | None = None) -> dict[str, dict]:
    """Backtest the whole book once on the legs' common bars (as the paper trader trades
    them) and compare each leg's direction and fill prices with its paper fills."""
    syms = acc.all_symbols
    frames = align({s: _bars(session, acc, s) for s in syms})
    specs = {s: to_spec(get_instrument(session, s)) for s in syms}
    bt = run_portfolio_backtest(frames, all_multi_strategies()[acc.strategy_key](), specs,
                                acc.params)
    ret = _window_return(bt.equity, pd.DatetimeIndex(bt.equity.index), since)
    out = {}
    for s in syms:
        ts = pd.DatetimeIndex(frames[s]["ts"])
        legs = bt.fills[bt.fills["symbol"] == s]
        units = pd.Series(0.0, index=ts)
        for t, u in zip(legs["ts"], legs["units"], strict=True):
            units[pd.Timestamp(t)] += u
        out[s] = _versus(frames[s], [f for f in fills if f.symbol == s], units.cumsum(), ret,
                         since)
    return out


def _versus(bars: pd.DataFrame, fills: list[PaperFill], bt_position: pd.Series,
            bt_return: float, since: datetime | None = None) -> dict:
    ts = pd.DatetimeIndex(bars["ts"])
    per_bar = pd.Series(0.0, index=ts)
    opens = pd.Series(bars["open"].to_numpy(), index=ts)
    gaps = []
    for f in fills:
        per_bar[pd.Timestamp(f.ts)] += f.qty
        if pd.Timestamp(f.ts) not in per_bar.index:
            continue
        o = opens.get(pd.Timestamp(f.ts))
        if o:
            gaps.append(abs(f.price / o - 1) * 1e4)
    paper_dir = np.sign(per_bar.cumsum().round(9))
    bt_dir = np.sign(pd.Series(bt_position.to_numpy(), index=ts).round(9))
    if since is not None:  # the account's own bars, less the first (see the module notes)
        keep = ts[ts > pd.Timestamp(since)][1:]
        paper_dir, bt_dir = paper_dir.reindex(keep), bt_dir.reindex(keep)
    p_chg, b_chg = _changes(paper_dir), _changes(bt_dir)
    missed, extra = sorted(b_chg - p_chg), sorted(p_chg - b_chg)
    return {
        "bars": len(bt_dir),
        "direction_agreement": round(float((paper_dir == bt_dir).mean()), 4) if len(bt_dir)
        else 0.0,
        "backtest_changes": len(b_chg), "paper_changes": len(p_chg),
        "missed_changes": len(missed), "extra_changes": len(extra),
        "missed_examples": [t.isoformat() for t in missed[:20]],
        "extra_examples": [t.isoformat() for t in extra[:20]],
        "max_fill_price_gap_bps": round(float(max(gaps)) if gaps else 0.0, 4),
        "backtest_return": bt_return,
    }


def compare(session: Session, name: str, since: datetime | None = None) -> dict:
    """Paper vs backtest for one account: on every bar it processed, or only on the bars
    after ``since`` (default: the account's own start, if it was created with start_now)."""
    acc = session.scalar(select(PaperAccount).where(PaperAccount.name == name))
    if acc is None:
        raise KeyError(name)
    since = since if since is not None else acc.starts_after
    syms = acc.all_symbols
    out: dict = {"account": name, "strategy": acc.strategy_key, "symbol": acc.symbol,
                 "symbols": syms, "simulated": True}
    if acc.last_bar_ts is None:
        return out | {"bars": 0, "consistent": None, "notes": ["account has not run yet"]}
    if since is not None and acc.last_bar_ts <= since:
        return out | {"bars": 0, "consistent": None,
                      "notes": [f"no bars processed after {since:%Y-%m-%d %H:%M} UTC yet"]}
    fills = session.scalars(select(PaperFill).where(PaperFill.account_id == acc.id)
                            .order_by(PaperFill.id)).all()
    multi = acc.strategy_key not in all_strategies()
    per = _multi(session, acc, fills, since) if multi else \
        {s: _one(session, acc, s, [f for f in fills if f.symbol == s], since) for s in syms}
    n = sum(p["bars"] for p in per.values())
    agree = sum(p["direction_agreement"] * p["bars"] for p in per.values()) / max(n, 1)
    gap = max(p["max_fill_price_gap_bps"] for p in per.values())
    total = {k: sum(p[k] for p in per.values()) for k in
             ("backtest_changes", "paper_changes", "missed_changes", "extra_changes")}

    notes = []
    if since is not None:
        notes.append(f"compared on the bars after {since:%Y-%m-%d %H:%M} UTC only, less the "
                     "first (a new account takes up a held position one bar later)")
    if acc.kill_engaged or "reset" in (acc.kill_reason or ""):
        notes.append(f"the kill switch has been engaged on this account ({acc.kill_reason}); "
                     "paper was flat while the backtest kept trading, which lowers agreement")
    if multi:
        notes.append("the whole book is backtested together on the bars where every leg "
                     "traded; paper legs are capped by the risk limits and skip small "
                     "rebalances, so only direction and timing are compared")
    elif len(syms) > 1:
        notes.append("each instrument is backtested on its own; account-wide limits "
                     "(gross, net and correlated exposure) can refuse entries a standalone "
                     "backtest takes")
    consistent = bool(agree >= MIN_AGREEMENT and gap <= MAX_PRICE_GAP_BPS)
    if agree < MIN_AGREEMENT:
        notes.append(f"direction agreement {agree:.1%} below {MIN_AGREEMENT:.0%}")
    elif gap > MAX_PRICE_GAP_BPS:
        notes.append(f"fill price gap {gap:.2f} bps above {MAX_PRICE_GAP_BPS} bps")
    one = per[syms[0]] if len(syms) == 1 else {}
    if multi:
        one = {"backtest_return": per[syms[0]]["backtest_return"]}
    return out | total | {
        "bars": n, "direction_agreement": round(agree, 4),
        "missed_examples": one.get("missed_examples", []),
        "extra_examples": one.get("extra_examples", []),
        "max_fill_price_gap_bps": gap,
        "backtest_return": one.get("backtest_return"),
        "per_symbol": per, "consistent": consistent, "notes": notes,
    }
