"""Trading calendars per market.

Each calendar answers two questions used across the platform:
  * ``is_open(ts)`` – is the market trading at this instant?
  * ``is_session_day(d)`` – is this calendar date a trading day (for daily bars)?

Each calendar also maps an instant to its ``session_date`` (the trading day it belongs to),
which daily resampling uses: an FX bar at Sunday 18:00 New York belongs to Monday.

Known simplifications (tracked in docs/STATUS.md): NYSE early closes (13:00 ET) follow the
regular rules (3 July, the day after Thanksgiving, 24 December); one-off full closures since
2000 are listed in ``NYSE_SPECIAL_CLOSURES``;
CME and FX holidays are approximated by full closures on 25 Dec and 1 Jan (plus Good Friday
for CME) and their early closes are ignored.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from apex.domain.market import Timeframe

NY = ZoneInfo("America/New_York")


class TradingCalendar:
    name = "base"

    def is_open(self, ts: datetime) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def is_session_day(self, d: date) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def session_date(self, ts: datetime) -> date:
        return ts.astimezone(UTC).date()

    def expected_bars(self, start: datetime, end: datetime, tf: Timeframe) -> Iterator[datetime]:
        """Yield expected bar open times in [start, end) (UTC)."""
        if tf is Timeframe.D1:
            d = start.astimezone(UTC).date()
            while datetime.combine(d, time(), UTC) < end:
                ts = datetime.combine(d, time(), UTC)
                if ts >= start and self.is_session_day(d):
                    yield ts
                d += timedelta(days=1)
            return
        step = tf.delta
        ts = _align(start, step)
        while ts < end:
            if ts >= start and self.is_open(ts):
                yield ts
            ts += step


def _align(ts: datetime, step: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    n = (ts - epoch) // step
    aligned = epoch + n * step
    return aligned if aligned >= ts else aligned + step


class Crypto24x7(TradingCalendar):
    name = "crypto_24x7"

    def is_open(self, ts: datetime) -> bool:
        return True

    def is_session_day(self, d: date) -> bool:
        return True


class Fx24x5(TradingCalendar):
    """Spot FX: Sunday 17:00 New York to Friday 17:00 New York."""

    name = "fx_24x5"

    def session_date(self, ts: datetime) -> date:
        return (ts.astimezone(NY) + timedelta(hours=7)).date()

    def is_open(self, ts: datetime) -> bool:
        if self.session_date(ts) in _fixed_closures(ts.year):
            return False
        local = ts.astimezone(NY)
        wd, t = local.weekday(), local.time()
        if wd == 5:
            return False
        if wd == 6:
            return t >= time(17)
        if wd == 4:
            return t < time(17)
        return True

    def is_session_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in _fixed_closures(d.year)


class CmeGlobex(TradingCalendar):
    """CME equity index futures (ES/MES/NQ/MNQ): Sun 18:00 to Fri 17:00 ET, daily halt 17-18."""

    name = "cme_globex"

    def session_date(self, ts: datetime) -> date:
        return (ts.astimezone(NY) + timedelta(hours=6)).date()

    def is_open(self, ts: datetime) -> bool:
        if not self.is_session_day(self.session_date(ts)):
            return False
        local = ts.astimezone(NY)
        wd, t = local.weekday(), local.time()
        if wd == 5:
            return False
        if wd == 6:
            return t >= time(18)
        if wd == 4:
            return t < time(17)
        return not (time(17) <= t < time(18))

    def is_session_day(self, d: date) -> bool:
        if d.weekday() >= 5 or d in _fixed_closures(d.year):
            return False
        return d != _easter(d.year) - timedelta(days=2)  # Good Friday


class UsEquityRth(TradingCalendar):
    """NYSE/Nasdaq regular trading hours 09:30-16:00 ET with full-day holidays."""

    name = "us_equity_rth"

    def session_date(self, ts: datetime) -> date:
        return ts.astimezone(NY).date()

    def is_open(self, ts: datetime) -> bool:
        local = ts.astimezone(NY)
        if not self.is_session_day(local.date()):
            return False
        close = time(13) if local.date() in nyse_early_closes(local.year) else time(16)
        return time(9, 30) <= local.time() < close

    def is_session_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in nyse_holidays(d.year)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    # Anonymous Gregorian algorithm
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return date(year, month, day)


def _fixed_closures(year: int) -> frozenset[date]:
    """Approximate full-day closures shared by FX and CME (and the neighbouring years'
    dates, since a session can straddle New Year)."""
    return frozenset({date(year, 12, 25), date(year, 1, 1), date(year + 1, 1, 1)})


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


# One-off full-day NYSE closures since 2000 (national days of mourning, 9/11, Hurricane
# Sandy). Extend when the exchange announces a new one.
NYSE_SPECIAL_CLOSURES: frozenset[date] = frozenset({
    date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),
    date(2004, 6, 11),  # President Reagan
    date(2007, 1, 2),  # President Ford
    date(2012, 10, 29), date(2012, 10, 30),  # Hurricane Sandy
    date(2018, 12, 5),  # President G. H. W. Bush
    date(2025, 1, 9),  # President Carter
})


@lru_cache(maxsize=64)
def nyse_holidays(year: int) -> frozenset[date]:
    days = {
        _nth_weekday(year, 1, 0, 3),  # MLK Day
        _nth_weekday(year, 2, 0, 3),  # Presidents' Day
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    ny = date(year, 1, 1)
    if ny.weekday() != 5:  # NYSE does not observe New Year on the prior Friday
        days.add(_observed(ny))
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    days |= {d for d in NYSE_SPECIAL_CLOSURES if d.year == year}
    return frozenset(days)


@lru_cache(maxsize=64)
def nyse_early_closes(year: int) -> frozenset[date]:
    """Regular 13:00 ET closes: 3 July and 24 December when they fall Monday to Thursday
    (on a Friday they are the observed holiday), and the day after Thanksgiving."""
    days = {_nth_weekday(year, 11, 3, 4) + timedelta(days=1)}
    for d in (date(year, 7, 3), date(year, 12, 24)):
        if d.weekday() < 4:
            days.add(d)
    return frozenset(days - nyse_holidays(year))


CALENDARS: dict[str, TradingCalendar] = {
    c.name: c for c in (Crypto24x7(), Fx24x5(), CmeGlobex(), UsEquityRth())
}


def get_calendar(name: str) -> TradingCalendar:
    try:
        return CALENDARS[name]
    except KeyError:
        raise KeyError(f"unknown trading calendar {name!r}; known: {sorted(CALENDARS)}") from None
