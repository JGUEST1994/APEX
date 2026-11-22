"""Replay 30-day practice runs of a basket over stored history, with the default safety limits.

One fresh practice account per calendar month (as if a 30-day practice run started on the 1st),
fed that month's bars CHUNK days at a time. Each time the kill switch trips, note when and why,
reset it (as a person checking every CHUNK days would) and carry on. A reset does not clear the
drawdown rule's high-water mark, so after a drawdown trip the account would trip again at once;
the replay then restarts the mark at the equity left, as opening a new account would.
Usage: python practice_replay.py SOURCE START SYMBOLS [STRATEGY] [CHUNK]
Needs APEX_DATABASE_URL (a database that already holds the bars) and APEX_TRADING_MODE=paper.
"""
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import func, select

from apex.core.config import get_settings
from apex.db.models import PaperAccount, PaperFill
from apex.db.session import make_engine, make_session_factory, session_scope
from apex.execution.store import create_account, reset_kill_switch, run_account

source, start, symbols = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
strategy = sys.argv[4] if len(sys.argv) > 4 else "MR-IBS-BASKET-001"
chunk = int(sys.argv[5]) if len(sys.argv) > 5 else 5
factory = make_session_factory(make_engine(get_settings().database_url))
now = datetime.now(UTC)
t0 = time.monotonic()


def kind(reason: str) -> str:
    return ("losing streak" if "losing trades" in reason else "daily loss" if "daily" in reason
            else "drawdown" if "drawdown" in reason else reason)


months = []  # (month start, days fed, return, stops [(time, reason)], round trips)
for m in pd.date_range(start, now.date(), freq="MS", tz="UTC"):
    m0, m1 = m.to_pydatetime(), (m + pd.offsets.MonthBegin(1)).to_pydatetime()
    name = f"replay-{m0:%Y-%m}"
    with session_scope(factory) as s:
        acc = create_account(s, name, strategy_key=strategy, symbol=symbols, timeframe="1d",
                             source=source)
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
    if days == 0:
        continue  # no prices for this month (yet)
    with session_scope(factory) as s:
        acc_id = s.scalar(select(PaperAccount.id).where(PaperAccount.name == name))
        fills = s.scalar(select(func.count()).select_from(PaperFill)
                         .where(PaperFill.account_id == acc_id))
    months.append((m0, days, equity / capital - 1, stops, fills / 2))

n = len(months)
all_stops = [st for *_, stops, _ in months for st in stops]
stopped = [mo for mo in months if mo[3]]
rets = [r for _, _, r, _, _ in months]
print(f"replayed {strategy} on {len(symbols)} markets as {n} fresh 30-day practice accounts, "
      f"{months[0][0]:%Y-%m} to {months[-1][0]:%Y-%m} (reset every {chunk} days at most; "
      f"{time.monotonic() - t0:.0f}s)")
print(f"months stopped at least once: {len(stopped)} of {n} ({len(stopped) / n:.0%}); "
      f"stops in all: {len(all_stops)}")
print("by reason:", dict(Counter(kind(r) for _, r in all_stops)))
print("months by number of stops:",
      dict(sorted(Counter(min(len(mo[3]), 3) for mo in months).items())), "(3 = 3 or more)")
print("months whose FIRST stop was:", dict(Counter(kind(mo[3][0][1]) for mo in stopped)))
streaks = sorted(int(r.split()[0]) for _, r in all_stops if "losing trades" in r)
if streaks:
    print(f"losing streak at the stop: min {streaks[0]}, median {streaks[len(streaks) // 2]}, "
          f"max {streaks[-1]} trades")
worst, best = min(months, key=lambda mo: mo[2]), max(months, key=lambda mo: mo[2])
print(f"monthly return: mean {statistics.mean(rets):+.2%}, median {statistics.median(rets):+.2%}, "
      f"{sum(r > 0 for r in rets) / n:.0%} of months up; worst {worst[2]:+.1%} ({worst[0]:%Y-%m}), "
      f"best {best[2]:+.1%} ({best[0]:%Y-%m}); about {sum(mo[4] for mo in months) / n:.1f} "
      f"round-trip trades a month")
print("year  months-stopped  streak  drawdown  daily  sum-of-monthly-returns")
for y in sorted({m0.year for m0, *_ in months}):
    ms = [mo for mo in months if mo[0].year == y]
    c = Counter(kind(r) for mo in ms for _, r in mo[3])
    print(f"{y}  {sum(bool(mo[3]) for mo in ms):5d} of {len(ms):2d}  {c['losing streak']:6d}  "
          f"{c['drawdown']:8d}  {c['daily loss']:5d}  {sum(mo[2] for mo in ms):+.1%}")
for at, why in all_stops:
    print(f"  {at:%Y-%m-%d}  {why}")
