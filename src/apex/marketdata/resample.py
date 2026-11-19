"""Resample bars to a coarser timeframe, respecting each market's trading calendar.

Intraday targets group by the target bar's open time (UTC-aligned buckets). Daily targets
group by the calendar's session date, so an FX or CME evening session counts towards the
next trading day, as exchanges and brokers report it. Daily bars are stamped 00:00 UTC of
the session date (APEX convention, see design 03).
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import pandas as pd

from apex.domain.market import Timeframe
from apex.marketdata.base import BAR_COLUMNS
from apex.marketdata.calendars import TradingCalendar

_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def resample_bars(df: pd.DataFrame, source_tf: Timeframe, target_tf: Timeframe,
                  calendar: TradingCalendar, *, drop_partial: bool = True) -> pd.DataFrame:
    if Timeframe.TICK in (source_tf, target_tf):
        raise ValueError("tick resampling is not supported")
    if target_tf.delta <= source_tf.delta or target_tf.delta % source_tf.delta:
        raise ValueError(f"cannot resample {source_tf.value} -> {target_tf.value}")
    if df.empty:
        return df[BAR_COLUMNS].copy()
    work = df[BAR_COLUMNS].sort_values("ts")
    if target_tf is Timeframe.D1:
        key = work["ts"].map(
            lambda t: datetime.combine(calendar.session_date(t.to_pydatetime()), time(), UTC))
    else:
        key = work["ts"].dt.floor(pd.Timedelta(target_tf.delta))
    grouped = work.groupby(key, sort=True)
    out = grouped.agg(_AGG)
    out["n"] = grouped.size()
    out.index.name = "ts"
    out = out.reset_index()
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    if drop_partial and target_tf is not Timeframe.D1:
        # a bucket is partial if the market was open for more source bars than we have
        expected = out["ts"].map(lambda t: sum(
            1 for _ in calendar.expected_bars(t.to_pydatetime(),
                                              t.to_pydatetime() + target_tf.delta, source_tf)))
        out = out[out["n"] >= expected]
    return out[BAR_COLUMNS].reset_index(drop=True)
