"""Intraday strategies close before the session ends instead of at the next session's open."""

import pandas as pd
import pytest

from apex.backtest.engine import run_backtest
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.strategies import indicators as ind
from apex.strategies.registry import all_strategies
from tests.conftest import utc

SESSION_FLAT = ("VWAP-MR-001", "BO-ORB-001", "VWAP-PB-001", "VWAP-BAND-001", "LIQ-PDHL-001")


def _ts(start, periods, freq="5min"):
    return pd.DataFrame({"ts": pd.date_range(start, periods=periods, freq=freq, tz="UTC")})


def test_bars_left_follows_the_calendar_across_dst_and_weekends():
    # 2025-03-07 (Fri, EST) and 2025-03-10 (Mon, EDT): RTH opens 14:30 then 13:30 UTC
    b = _ts("2025-03-07 14:30", 4 * 24 * 12)
    left = ind.bars_left_in_session(b, "us_equity_rth")
    ny = b["ts"].dt.tz_convert("America/New_York")
    rth = (ny.dt.time >= pd.Timestamp("09:30").time()) & (ny.dt.time < pd.Timestamp("16:00").time())
    last = b["ts"][rth & (left == 0)].dt.tz_convert("America/New_York")
    assert list(last.dt.strftime("%a %H:%M")) == ["Fri 15:55", "Mon 15:55"]
    assert (left[~rth] == 10_000).all()  # outside the session: nothing to plan for
    mon = b["ts"] == pd.Timestamp("2025-03-10 13:30", tz="UTC")
    assert left[mon].item() == 77  # first bar of a 78-bar day; the first bar ever is unknown
    # CME Globex: session ends 17:00 ET; 15-minute bars
    es = _ts("2025-03-04 12:00", 200, "15min")
    l2 = ind.bars_left_in_session(es, "cme_globex")
    at = es["ts"].dt.tz_convert("America/New_York").dt.strftime("%H:%M")
    assert set(at[l2 == 0]) == {"16:45"}
    # early close (day after Thanksgiving 2025): the last bar is 12:55 ET
    ff = _ts("2025-11-28 14:30", 60)
    l3 = ind.bars_left_in_session(ff, "us_equity_rth")
    assert ff["ts"][l3 == 0].dt.tz_convert("America/New_York").dt.strftime("%H:%M").tolist() \
        == ["12:55"]
    # daily bars never get flattened
    d = _ts("2025-01-01", 30, "1D")
    assert (ind.bars_left_in_session(d, "us_equity_rth") == 10_000).all()


@pytest.mark.parametrize("key", SESSION_FLAT)
def test_nothing_held_over_the_last_bar_or_overnight(key):
    spy = by_symbol()["SPY"]
    bars = SyntheticProvider(annual_vol=0.6).fetch_bars(spy, Timeframe.M5, utc(2025, 3, 3),
                                                        utc(2025, 3, 21))
    strat = all_strategies()[key]()
    p = strat.params()
    assert p["flat_bars"] == 1 and strat.meta.version == "0.2.0"
    pos = strat.target_position(bars, p)
    left = ind.bars_left_in_session(bars, p["calendar"])
    assert (pos[left <= 1] == 0).all()
    # in the backtest, every position is closed by the open of the session's last bar
    res = run_backtest(bars, strat, spy)
    ny = lambda t: pd.Timestamp(t).tz_convert("America/New_York")  # noqa: E731
    for t in res.trades:
        assert ny(t.exit_ts).date() == ny(t.entry_ts).date(), "held overnight"
        assert ny(t.exit_ts).strftime("%H:%M") <= "15:55"
    # only the closing bars change; flat_bars=0 gives the old behaviour back
    old = strat.target_position(bars, strat.params({"flat_bars": 0}))
    assert (pos[left > 1] == old[left > 1]).all()
    assert (old[left <= 1] != 0).any()  # the old rule really did hold into the close
