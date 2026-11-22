"""Replay 30-day practice runs of a basket over stored history.

One fresh practice account per calendar month (as if a 30-day practice run started on the 1st),
fed that month's bars CHUNK days at a time. Each time the kill switch trips, note when and why,
reset it (as a person checking every CHUNK days would) and carry on. A reset does not clear the
drawdown rule's high-water mark, so after a drawdown trip the account would trip again at once;
the replay then restarts the mark at the equity left, as opening a new account would.
Each month is also backtested from flat on the same bars, to compare returns and to measure how
often practice and backtest hold the same direction (the practice record needs 95%).
LIMITS "loose" lifts the correlated-group cap and the position count (a what-if, never a
setting), to show what those two limits cost.
Usage: python practice_replay.py SOURCE START SYMBOLS [STRATEGY] [CHUNK] [LIMITS]
Needs APEX_DATABASE_URL (a database that already holds the bars) and APEX_TRADING_MODE=paper.
"""
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from apex.backtest.portfolio import run_portfolio_backtest
from apex.core.config import get_settings
from apex.db.models import PaperAccount, PaperFill
from apex.db.session import make_engine, make_session_factory, session_scope
from apex.domain.market import Timeframe
from apex.execution.store import create_account, reset_kill_switch, run_account
from apex.marketdata.repository import get_instrument, load_bars, to_spec
from apex.risk.limits import RiskLimits
from apex.strategies.multi import align, all_multi_strategies

source, start, symbols = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
strategy = sys.argv[4] if len(sys.argv) > 4 else "MR-IBS-BASKET-001"
chunk = int(sys.argv[5]) if len(sys.argv) > 5 else 5
loose = len(sys.argv) > 6 and sys.argv[6] == "loose"
limits = (RiskLimits(max_cluster_exposure=1.0, max_positions=len(symbols)) if loose
          else RiskLimits())
factory = make_session_factory(make_engine(get_settings().database_url))
now = datetime.now(UTC)
t0 = time.monotonic()
with session_scope(factory) as s:
    frames = align({sym: load_bars(s, get_instrument(s, sym).id, Timeframe("1d"), source,
                                   None, None) for sym in symbols})
    specs = {sym: to_spec(get_instrument(s, sym)) for sym in symbols}
basket = all_multi_strategies()[strategy]()


def kind(reason: str) -> str:
    return ("losing streak" if "losing trades" in reason else "daily loss" if "daily" in reason
            else "drawdown" if "drawdown" in reason else reason)


def directions(ts: pd.DatetimeIndex, fills) -> pd.Series:  # noqa: ANN001
    units = pd.Series(0.0, index=ts)
    for t, q in fills:
        if pd.Timestamp(t) in units.index:
            units[pd.Timestamp(t)] += q
    return np.sign(units.cumsum().round(9))


months = []  # (start, days, return, stops, agreement, backtest return)
for m in pd.date_range(start, now.date(), freq="MS", tz="UTC"):
    m0, m1 = m.to_pydatetime(), (m + pd.offsets.MonthBegin(1)).to_pydatetime()
    name = f"replay-{m0:%Y-%m}"
    with session_scope(factory) as s:
        acc = create_account(s, name, strategy_key=strategy, symbol=symbols, timeframe="1d",
                             source=source, limits=limits)
        acc.last_bar_ts = m0 - timedelta(seconds=1)  # the month's bars only, from the 1st
        capital = acc.initial_capital
    stops, days, equity = [], 0, capital
    while True:
        with session_scope(factory) as s:
            res = run_account(s, name, now=now, end=m1, max_bars=chunk)
            if res["bars"] == 0:
                break
            days, equity = days + res["bars"], res["equity"]
            acc = s.scalar(select(PaperAccount).where(PaperAccount.name == name))
            if acc.kill_engaged:
                reason = acc.kill_reason
                stops.append((acc.kill_at, reason))
                reset_kill_switch(s, name, "replay", now)
                if "drawdown" in reason:
                    acc.peak_equity = equity
    month = {sym: f[(f["ts"] >= m0) & (f["ts"] < m1)].reset_index(drop=True)
             for sym, f in frames.items()}
    ts = pd.DatetimeIndex(month[symbols[0]]["ts"])
    if days == 0 or len(ts) < 2:
        continue  # no prices for this month (yet)
    bt = run_portfolio_backtest(month, basket, specs)  # from flat on the 1st, like the account
    with session_scope(factory) as s:
        acc_id = s.scalar(select(PaperAccount.id).where(PaperAccount.name == name))
        fills = s.execute(select(PaperFill.ts, PaperFill.symbol, PaperFill.qty)
                          .where(PaperFill.account_id == acc_id)).all()
    same = total = 0
    for sym in symbols:
        legs = bt.fills[bt.fills["symbol"] == sym]
        b = directions(ts, zip(legs["ts"], legs["units"], strict=True))
        p = directions(ts, [(t, q) for t, sy, q in fills if sy == sym])
        same, total = same + int((p == b).sum()), total + len(ts)
    months.append((m0, days, equity / capital - 1, stops, same / total,
                   float(bt.equity.iloc[-1] / bt.equity.iloc[0] - 1)))

n = len(months)
all_stops = [st for mo in months for st in mo[3]]
stopped = [mo for mo in months if mo[3]]
rets, bts, agree = ([mo[2] for mo in months], [mo[5] for mo in months],
                    sorted(mo[4] for mo in months))
passed = [mo for mo in months if not mo[3] and mo[4] >= 0.95]
label = ("LOOSE what-if: no correlated-group cap, a position for every fund" if loose
         else "default safety limits")
print(f"replayed {strategy} on {len(symbols)} markets as {n} fresh 30-day practice accounts, "
      f"{months[0][0]:%Y-%m} to {months[-1][0]:%Y-%m} ({label}; reset every {chunk} days "
      f"at most; {time.monotonic() - t0:.0f}s)")
print(f"months stopped at least once: {len(stopped)} of {n} ({len(stopped) / n:.0%}); "
      f"stops in all: {len(all_stops)}; by reason:",
      dict(Counter(kind(r) for _, r in all_stops)))
print(f"same direction as the backtest: median {statistics.median(agree):.1%}, worst tenth "
      f"below {agree[n // 10]:.1%}; months at or above 95%: "
      f"{sum(a >= 0.95 for a in agree)} of {n}")
print(f"months that would pass the practice record (no stop, >= 95% agreement): {len(passed)} "
      f"of {n} ({len(passed) / n:.0%})")
print(f"monthly return, practice: mean {statistics.mean(rets):+.2%}, median "
      f"{statistics.median(rets):+.2%}, {sum(r > 0 for r in rets) / n:.0%} of months up; "
      f"backtest from flat on the same months: mean {statistics.mean(bts):+.2%}, median "
      f"{statistics.median(bts):+.2%}")
worst, best = min(months, key=lambda mo: mo[2]), max(months, key=lambda mo: mo[2])
print(f"practice worst month {worst[2]:+.1%} ({worst[0]:%Y-%m}), best {best[2]:+.1%} "
      f"({best[0]:%Y-%m})")
print("year  stopped  passed  agree(median)  practice-sum  backtest-sum")
for y in sorted({mo[0].year for mo in months}):
    ms = [mo for mo in months if mo[0].year == y]
    print(f"{y}  {sum(bool(mo[3]) for mo in ms):3d}/{len(ms):<2d}  "
          f"{sum(mo in passed for mo in ms):3d}/{len(ms):<2d}  "
          f"{statistics.median(mo[4] for mo in ms):12.1%}  {sum(mo[2] for mo in ms):+11.1%}  "
          f"{sum(mo[5] for mo in ms):+11.1%}")
for at, why in all_stops:
    print(f"  {at:%Y-%m-%d}  {why}")
