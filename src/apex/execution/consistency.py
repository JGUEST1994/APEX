"""Paper-vs-backtest consistency report (Phase 9).

Re-runs the backtester over exactly the bars a paper account has processed, with the same
strategy and parameters, and compares the two. Paper sizes are risk-capped and resized
every bar, so the comparison is on *direction* (long / flat / short per bar), entry/exit
timing and fill prices rather than on unit counts. A strategy should not be approved by a
human while its paper record disagrees with its backtest.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.backtest.engine import run_backtest
from apex.db.models import PaperAccount, PaperFill
from apex.domain.market import Timeframe
from apex.marketdata.repository import get_instrument, load_bars, to_spec
from apex.strategies.registry import all_strategies

MIN_AGREEMENT = 0.95  # share of bars where paper and backtest hold the same direction
MAX_PRICE_GAP_BPS = 1.0  # paper and backtest both fill at the bar open; should be ~equal


def _changes(direction: pd.Series) -> set:
    d = direction.to_numpy()
    idx = np.flatnonzero(np.diff(d, prepend=0) != 0)
    return {direction.index[i] for i in idx}


def _one(session: Session, acc: PaperAccount, sym: str, fills: list[PaperFill]) -> dict:
    inst = get_instrument(session, sym)
    bars = load_bars(session, inst.id, Timeframe(acc.timeframe), acc.source, None, None)
    bars = bars[bars["ts"] <= acc.last_bar_ts].reset_index(drop=True)
    bt = run_backtest(bars, all_strategies()[acc.strategy_key](), to_spec(inst), acc.params)
    ts = pd.DatetimeIndex(bars["ts"])
    per_bar = pd.Series(0.0, index=ts)
    opens = pd.Series(bars["open"].to_numpy(), index=ts)
    gaps = []
    for f in fills:
        per_bar[pd.Timestamp(f.ts)] += f.qty
        o = opens.get(pd.Timestamp(f.ts))
        if o:
            gaps.append(abs(f.price / o - 1) * 1e4)
    paper_dir = np.sign(per_bar.cumsum().round(9))
    bt_dir = np.sign(pd.Series(bt.position.to_numpy(), index=ts).round(9))
    p_chg, b_chg = _changes(paper_dir), _changes(bt_dir)
    missed, extra = sorted(b_chg - p_chg), sorted(p_chg - b_chg)
    return {
        "bars": len(bars), "direction_agreement": round(float((paper_dir == bt_dir).mean()), 4),
        "backtest_changes": len(b_chg), "paper_changes": len(p_chg),
        "missed_changes": len(missed), "extra_changes": len(extra),
        "missed_examples": [t.isoformat() for t in missed[:20]],
        "extra_examples": [t.isoformat() for t in extra[:20]],
        "max_fill_price_gap_bps": round(float(max(gaps)) if gaps else 0.0, 4),
        "backtest_return": float(bt.equity.iloc[-1] / bt.equity.iloc[0] - 1),
    }


def compare(session: Session, name: str) -> dict:
    acc = session.scalar(select(PaperAccount).where(PaperAccount.name == name))
    if acc is None:
        raise KeyError(name)
    syms = acc.all_symbols
    out: dict = {"account": name, "strategy": acc.strategy_key, "symbol": acc.symbol,
                 "symbols": syms, "simulated": True}
    if acc.last_bar_ts is None:
        return out | {"bars": 0, "consistent": None, "notes": ["account has not run yet"]}
    fills = session.scalars(select(PaperFill).where(PaperFill.account_id == acc.id)
                            .order_by(PaperFill.id)).all()
    per = {s: _one(session, acc, s, [f for f in fills if f.symbol == s]) for s in syms}
    n = sum(p["bars"] for p in per.values())
    agree = sum(p["direction_agreement"] * p["bars"] for p in per.values()) / max(n, 1)
    gap = max(p["max_fill_price_gap_bps"] for p in per.values())
    total = {k: sum(p[k] for p in per.values()) for k in
             ("backtest_changes", "paper_changes", "missed_changes", "extra_changes")}

    notes = []
    if acc.kill_engaged or "reset" in (acc.kill_reason or ""):
        notes.append(f"the kill switch has been engaged on this account ({acc.kill_reason}); "
                     "paper was flat while the backtest kept trading, which lowers agreement")
    if len(syms) > 1:
        notes.append("each instrument is backtested on its own; account-wide limits "
                     "(gross, net and correlated exposure) can refuse entries a standalone "
                     "backtest takes")
    consistent = bool(agree >= MIN_AGREEMENT and gap <= MAX_PRICE_GAP_BPS)
    if agree < MIN_AGREEMENT:
        notes.append(f"direction agreement {agree:.1%} below {MIN_AGREEMENT:.0%}")
    elif gap > MAX_PRICE_GAP_BPS:
        notes.append(f"fill price gap {gap:.2f} bps above {MAX_PRICE_GAP_BPS} bps")
    one = per[syms[0]] if len(syms) == 1 else {}
    return out | total | {
        "bars": n, "direction_agreement": round(agree, 4),
        "missed_examples": one.get("missed_examples", []),
        "extra_examples": one.get("extra_examples", []),
        "max_fill_price_gap_bps": gap,
        "backtest_return": one.get("backtest_return"),
        "per_symbol": per, "consistent": consistent, "notes": notes,
    }
