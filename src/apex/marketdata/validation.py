"""Market data validation (Section 10): duplicates, OHLC sanity, gaps, spikes, staleness.

``validate_bars`` never mutates its input. It returns a report listing every issue and a
``clean`` frame containing only rows safe to store: duplicates collapsed (first wins) and
rows with errors removed. Warnings (gaps, spikes) are recorded but the rows are kept,
because a real market can legitimately gap or spike; strategies decide how to treat them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from apex.domain.market import InstrumentSpec, Timeframe
from apex.marketdata.calendars import TradingCalendar, get_calendar

ERROR, WARNING = "error", "warning"


@dataclass(frozen=True)
class Issue:
    kind: str
    severity: str
    ts: datetime | None
    detail: str = ""


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)
    clean: pd.DataFrame | None = None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    def count(self, kind: str) -> int:
        return sum(1 for i in self.issues if i.kind == kind)


def validate_bars(
    df: pd.DataFrame,
    spec: InstrumentSpec,
    timeframe: Timeframe,
    *,
    calendar: TradingCalendar | None = None,
    expected_range: tuple[datetime, datetime] | None = None,
    spike_sigma: float = 12.0,
) -> ValidationReport:
    cal = calendar or get_calendar(spec.calendar)
    report = ValidationReport()
    bad = pd.Series(False, index=df.index)

    def add(mask: pd.Series, kind: str, severity: str, detail: str) -> None:
        nonlocal bad
        for ts in df.loc[mask, "ts"]:
            report.issues.append(Issue(kind, severity, ts.to_pydatetime(), detail))
        if severity == ERROR:
            bad = bad | mask

    prices = df[["open", "high", "low", "close"]]
    add(prices.isna().any(axis=1) | df["volume"].isna(), "missing_value", ERROR,
        "NaN in OHLCV")
    add((prices <= 0).any(axis=1), "non_positive_price", ERROR, "price <= 0")
    add(df["volume"] < 0, "negative_volume", ERROR, "volume < 0")
    hi_bad = df["high"] < df[["open", "close", "low"]].max(axis=1)
    lo_bad = df["low"] > df[["open", "close", "high"]].min(axis=1)
    add(hi_bad | lo_bad, "ohlc_inconsistent", ERROR, "high/low do not bound open/close")

    dup = df["ts"].duplicated(keep="first")
    conflicting = dup & ~df.duplicated(subset=["ts", "open", "high", "low", "close"], keep="first")
    add(dup & ~conflicting, "duplicate", WARNING, "exact duplicate dropped")
    add(conflicting, "duplicate_conflict", ERROR, "same timestamp, different values")
    bad = bad | dup

    if timeframe is not Timeframe.TICK and len(df):
        if timeframe is Timeframe.D1:
            misaligned = df["ts"] != df["ts"].dt.normalize()
            closed = df["ts"].map(lambda t: not cal.is_session_day(t.date()))
        else:
            misaligned = df["ts"] != df["ts"].dt.floor(pd.Timedelta(timeframe.delta))
            closed = df["ts"].map(lambda t: not cal.is_open(t.to_pydatetime()))
        add(misaligned, "misaligned_timestamp", ERROR, f"not on a {timeframe.value} boundary")
        add(closed & ~misaligned, "outside_session", WARNING, f"market closed per {cal.name}")

    clean = df[~bad].reset_index(drop=True)

    if timeframe is not Timeframe.TICK:
        lo, hi = expected_range if expected_range else (
            (clean["ts"].iloc[0].to_pydatetime(), clean["ts"].iloc[-1].to_pydatetime()
             + timedelta(microseconds=1)) if len(clean) else (None, None))
        if lo is not None:
            have = set(clean["ts"].map(lambda t: t.to_pydatetime()))
            missing = [t for t in cal.expected_bars(lo, hi, timeframe) if t not in have]
            for start, end, n in _group_runs(missing, timeframe):
                detail = f"{n} missing bar(s) {start.isoformat()} .. {end.isoformat()}"
                report.issues.append(Issue("gap", WARNING, start, detail))

    if len(clean) > 20:
        lr = np.log(clean["close"]).diff()
        mad = (lr - lr.median()).abs().median()
        if mad > 0:
            robust_z = (lr - lr.median()).abs() / (1.4826 * mad)
            spikes = robust_z > spike_sigma
            for ts, z in zip(clean.loc[spikes, "ts"], robust_z[spikes], strict=True):
                report.issues.append(Issue("spike", WARNING, ts.to_pydatetime(),
                                           f"return {z:.1f} robust sigmas"))

    report.clean = clean
    return report


def _group_runs(times: list[datetime], tf: Timeframe):
    """Collapse consecutive missing bars into (first, last, count) runs."""
    if not times:
        return
    step = tf.delta
    first = prev = times[0]
    n = 1
    for t in times[1:]:
        if t - prev == step or (tf is Timeframe.D1 and t - prev <= timedelta(days=4)):
            prev, n = t, n + 1
            continue
        yield first, prev, n
        first = prev = t
        n = 1
    yield first, prev, n


def is_stale(last_bar_ts: datetime | None, timeframe: Timeframe, now: datetime,
             calendar: TradingCalendar, max_lag_seconds: int) -> bool:
    """True if a live feed should be considered stale (and trading suspended).

    A feed is stale when the market is open and the newest bar's close is older than
    ``max_lag_seconds``. Missing data while the market is open is always stale. A closed
    market is never stale.
    """
    if not calendar.is_open(now):
        return False
    if last_bar_ts is None:
        return True
    bar_close = last_bar_ts + (timeframe.delta if timeframe is not Timeframe.TICK else timedelta())
    return (now - bar_close).total_seconds() > max_lag_seconds
