from datetime import UTC, date, datetime

import pytest

from apex.domain.market import Timeframe
from apex.marketdata.calendars import get_calendar, nyse_holidays
from tests.conftest import utc


def test_nyse_holidays_2025():
    h = nyse_holidays(2025)
    for d in [date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
              date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
              date(2025, 11, 27), date(2025, 12, 25),
              date(2025, 1, 9)]:  # day of mourning for President Carter
        assert d in h, d
    assert len(h) == 11


def test_nyse_observed_rules():
    assert date(2021, 12, 31) not in nyse_holidays(2022)  # NY on Saturday: not observed Friday
    assert date(2022, 12, 26) in nyse_holidays(2022)  # Christmas Sunday -> Monday
    assert date(2026, 7, 3) in nyse_holidays(2026)  # July 4 Saturday -> Friday


@pytest.mark.parametrize("cal,ts,open_", [
    ("us_equity_rth", utc(2025, 3, 10, 13, 29), False),  # 09:29 EDT
    ("us_equity_rth", utc(2025, 3, 10, 13, 30), True),
    ("us_equity_rth", utc(2025, 3, 7, 14, 30), True),  # 09:30 EST (before DST)
    ("us_equity_rth", utc(2025, 4, 18, 15, 0), False),  # Good Friday
    ("fx_24x5", utc(2025, 3, 8, 12, 0), False),  # Saturday
    ("fx_24x5", utc(2025, 3, 9, 20, 59), False),  # Sun 16:59 EDT
    ("fx_24x5", utc(2025, 3, 9, 21, 0), True),  # Sun 17:00 EDT
    ("fx_24x5", utc(2025, 3, 7, 22, 0), False),  # Fri 17:00 EST
    ("cme_globex", utc(2025, 3, 11, 21, 30), False),  # 17:30 EDT daily halt
    ("cme_globex", utc(2025, 3, 11, 22, 0), True),
    ("crypto_24x7", utc(2025, 3, 8, 3, 0), True),
])
def test_is_open(cal, ts, open_):
    assert get_calendar(cal).is_open(ts) is open_


def test_expected_bars_equity_hourly_day():
    bars = list(get_calendar("us_equity_rth").expected_bars(
        utc(2025, 3, 10), utc(2025, 3, 11), Timeframe.M30))
    assert len(bars) == 13  # 09:30..15:30 ET
    assert bars[0] == utc(2025, 3, 10, 13, 30)


def test_expected_daily_bars_skip_weekend():
    days = list(get_calendar("fx_24x5").expected_bars(utc(2025, 3, 7), utc(2025, 3, 11), Timeframe.D1))
    assert [d.day for d in days] == [7, 10]


def test_unknown_calendar():
    with pytest.raises(KeyError):
        get_calendar("nope")


def test_nyse_early_closes():
    from apex.marketdata.calendars import nyse_early_closes

    # published NYSE 13:00 closes
    assert nyse_early_closes(2024) == {date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24)}
    assert nyse_early_closes(2025) == {date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24)}
    # 3 July 2026 and 24 December 2021 are Fridays: observed holidays, not early closes
    assert nyse_early_closes(2026) == {date(2026, 11, 27), date(2026, 12, 24)}
    assert nyse_early_closes(2021) == {date(2021, 11, 26)}
    cal = get_calendar("us_equity_rth")
    assert cal.is_open(datetime(2025, 11, 28, 17, 55, tzinfo=UTC))  # 12:55 ET
    assert not cal.is_open(datetime(2025, 11, 28, 18, 0, tzinfo=UTC))  # 13:00 ET
    bars = list(cal.expected_bars(datetime(2025, 12, 24, tzinfo=UTC),
                                  datetime(2025, 12, 25, tzinfo=UTC), Timeframe.M30))
    assert len(bars) == 7  # 09:30 to 13:00


def test_nyse_one_off_closures():
    cal = get_calendar("us_equity_rth")
    for d in (date(2012, 10, 29), date(2012, 10, 30), date(2018, 12, 5), date(2001, 9, 14)):
        assert not cal.is_session_day(d), d
    assert cal.is_session_day(date(2012, 10, 31)) and cal.is_session_day(date(2001, 9, 17))
    # the special closure must not leak into other years
    assert date(2018, 12, 5) not in nyse_holidays(2019)
