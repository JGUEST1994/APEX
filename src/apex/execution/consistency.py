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


def compare(session: Session, name: str) -> dict:
    acc = session.scalar(select(PaperAccount).where(PaperAccount.name == name))
    if acc is None:
        raise KeyError(name)
    out: dict = {"account": name, "strategy": acc.strategy_key, "symbol": acc.symbol,
                 "simulated": True}
    if acc.last_bar_ts is None:
        return out | {"bars": 0, "consistent": None, "notes": ["account has not run yet"]}
    inst = get_instrument(session, acc.symbol)
    spec = to_spec(inst)
    bars = load_bars(session, inst.id, Timeframe(acc.timeframe), acc.source, None, None)
    bars = bars[bars["ts"] <= acc.last_bar_ts].reset_index(drop=True)
    strat = all_strategies()[acc.strategy_key]()
    bt = run_backtest(bars, strat, spec, acc.params)

    fills = session.scalars(select(PaperFill).where(PaperFill.account_id == acc.id)
                            .order_by(PaperFill.id)).all()
    ts = pd.DatetimeIndex(bars["ts"])
    per_bar = pd.Series(0.0, index=ts)
    for f in fills:
        per_bar[pd.Timestamp(f.ts)] += f.qty
    paper_dir = np.sign(per_bar.cumsum().round(9))
    bt_dir = np.sign(pd.Series(bt.position.to_numpy(), index=ts).round(9))

    agree = float((paper_dir == bt_dir).mean())
    p_chg, b_chg = _changes(paper_dir), _changes(bt_dir)
    missed = sorted(b_chg - p_chg)
    extra = sorted(p_chg - b_chg)

    gaps = []
    opens = pd.Series(bars["open"].to_numpy(), index=ts)
    for f in fills:
        o = opens.get(pd.Timestamp(f.ts))
        if o:
            gaps.append(abs(f.price / o - 1) * 1e4)
    gap = float(max(gaps)) if gaps else 0.0

    kill_note = []
    if acc.kill_engaged or "reset" in (acc.kill_reason or ""):
        kill_note = ["the kill switch has been engaged on this account; paper was flat "
                     "while the backtest kept trading, which lowers agreement"]
    streak_cap = (acc.limits or {}).get("max_consecutive_losses")
    if streak_cap is not None and acc.consecutive_losses >= streak_cap:
        kill_note.append(f"{acc.consecutive_losses} losing trades in a row hit the risk "
                         f"limit ({streak_cap}); new trades are refused, so paper stops "
                         "following the backtest")
    consistent = bool(agree >= MIN_AGREEMENT and gap <= MAX_PRICE_GAP_BPS)
    notes = kill_note + ([] if consistent else
                         [f"direction agreement {agree:.1%} below {MIN_AGREEMENT:.0%}"
                          if agree < MIN_AGREEMENT else
                          f"fill price gap {gap:.2f} bps above {MAX_PRICE_GAP_BPS} bps"])
    iso = [t.isoformat() for t in missed[:20]], [t.isoformat() for t in extra[:20]]
    return out | {
        "bars": len(bars), "direction_agreement": round(agree, 4),
        "backtest_changes": len(b_chg), "paper_changes": len(p_chg),
        "missed_changes": len(missed), "extra_changes": len(extra),
        "missed_examples": iso[0], "extra_examples": iso[1],
        "max_fill_price_gap_bps": round(float(gap), 4),
        "backtest_return": float(bt.equity.iloc[-1] / bt.equity.iloc[0] - 1),
        "consistent": consistent, "notes": notes,
    }
