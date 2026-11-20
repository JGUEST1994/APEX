"""Batch 4: H15 Asia extension reversion, H32 Asia range sweep, H34 equal highs/lows raid,
H36 change of character, H46 daily-bias ORB, H47 weekly level + daily engulfing, H51 micro
pullback scalp. (Look-ahead and "trades on some data" checks for these run in
test_strategies_research_api.py with every registered strategy.)"""

import numpy as np
import pandas as pd

from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.strategies import indicators as ind
from apex.strategies.builtin4 import _swings
from apex.strategies.registry import all_strategies
from tests.conftest import utc

S = all_strategies()


def _run(key, bars, **over):
    s = S[key]()
    return s.target_position(bars, s.params(over))


def _bars(ts, close, wick=0.1):
    """Open = previous close, high/low = body +/- ``wick`` (so peaks are never tied)."""
    close = np.asarray(close, float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"ts": ts, "open": open_, "high": np.maximum(open_, close) + wick,
                         "low": np.minimum(open_, close) - wick, "close": close,
                         "volume": 1.0})


def _path(*points):
    """Piecewise-linear close path through ``points`` in steps of 1."""
    out = [float(points[0])]
    for a, b in zip(points, points[1:], strict=False):
        step = 1.0 if b > a else -1.0
        out += list(np.arange(a + step, b + step / 2, step))
    return np.array(out)


def test_swings_are_confirmed_only_k_bars_later():
    close = _path(100, 105, 100)  # one peak at index 5
    bars = _bars(pd.date_range("2025-03-03", periods=len(close), freq="1h", tz="UTC"),
                 close, wick=0.0)
    sh, _ = _swings(bars, 3)
    assert np.isnan(sh[:8, 0]).all()  # peak at 5 is known from bar 8 on
    assert sh[8, 0] == 105 and np.isnan(sh[8, 1])


def test_asia_extension_fades_a_wide_asia_move_only_in_the_london_window():
    ts = pd.date_range("2025-03-03", periods=96 * 8, freq="15min", tz="UTC")
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.03, len(ts))
    last = ts.floor("D") == ts[-1].floor("D")
    r[last & (ts.hour < 7)] = 0.1  # strong, wide Asia rally on the final day
    r[last & (ts.hour >= 7)] = 0.0
    bars = _bars(ts, 100 + np.cumsum(r), wick=0.02)
    pos = _run("MR-ASIA-001", bars)
    hour = pd.Series(ts.hour, index=bars.index)
    assert (pos[(hour < 7) | (hour >= 10)] == 0).all()
    entry = np.flatnonzero(last & (ts.hour == 7) & (ts.minute == 0))[0]
    assert pos.iloc[entry] == -1  # fade the rally
    assert (_run("MR-ASIA-001", bars, allow_short=False) >= 0).all()


def test_asia_sweep_fades_one_run_through_the_asia_high_per_day():
    ts = pd.date_range("2025-03-03", periods=96, freq="15min", tz="UTC")
    bars = pd.DataFrame({"ts": ts, "open": 100.0, "high": 100.5, "low": 99.5,
                         "close": 100.0, "volume": 1.0})
    i8, i9 = 32, 36  # 08:00 and 09:00
    bars.loc[i8, ["high", "close"]] = [101.0, 100.2]  # runs the Asia high, closes inside
    bars.loc[i9, ["high", "close"]] = [100.8, 100.2]  # a second sweep is ignored
    pos = _run("LIQ-ASIA-001", bars)
    assert (pos[:i8] == 0).all()
    assert (pos[i8:64] == -1).all()  # held until the 16:00 flat time
    assert (pos[64:] == 0).all()


def test_equal_highs_raid_is_faded():
    pre = np.tile([100.0, 100.5], 12)
    close = np.concatenate([pre, _path(100, 103, 100, 103, 100)])
    close = np.append(close, 102.5)
    ts = pd.date_range("2025-03-03", periods=len(close), freq="1h", tz="UTC")
    bars = _bars(ts, close)
    bars.loc[len(close) - 1, "high"] = 103.5  # runs the equal highs at 103.1, closes below
    pos = _run("LIQ-EQH-001", bars)
    assert pos.iloc[-1] == -1
    assert (pos.iloc[:-1] == 0).all()


def test_change_of_character_shorts_the_break_of_an_uptrend():
    close = _path(100, 106, 103, 109, 106, 112, 100)
    ts = pd.date_range("2025-03-03", periods=len(close), freq="15min", tz="UTC")
    bars = pd.DataFrame({"ts": ts, "open": close, "high": close + 0.2, "low": close - 0.2,
                         "close": close, "volume": 1.0})  # no ties at swing points
    pos = _run("MS-CHOCH-001", bars)
    brk = int(np.flatnonzero((close < 105.8) & (np.arange(len(close)) > 20))[0])
    assert pos.iloc[brk] == -1 and (pos.iloc[:brk] == 0).all()
    assert (_run("MS-CHOCH-001", bars, allow_short=False) == 0).all()


def test_daily_bias_orb_trades_after_the_range_and_is_flat_at_the_close():
    bars = SyntheticProvider(annual_vol=0.6).fetch_bars(by_symbol()["SPY"], Timeframe.M5,
                                                        utc(2025, 2, 3), utc(2025, 3, 14))
    pos = _run("MTF-ORB-001", bars)
    assert (pos != 0).any()
    n = bars.groupby(ind.session_keys(bars, "us_equity_rth")).cumcount()
    assert (pos[n < 6] == 0).all()  # never inside the opening range
    assert (pos[ind.bars_left_in_session(bars, "us_equity_rth") < 1] == 0).all()
    assert (_run("MTF-ORB-001", bars, allow_short=False) >= 0).all()


def test_weekly_level_engulfing_buys_a_bullish_engulf_at_last_weeks_low():
    ts = pd.date_range("2025-03-03", periods=12, freq="B", tz="UTC")  # Mon 3 Mar onward
    rows = [(102, 103, 100, 102.5), (102.5, 104, 102, 103), (103, 103.5, 101, 101.5),
            (101.5, 102.5, 101, 102), (102, 103, 101.5, 102),  # week 1: low 100, high 104
            (102, 102.5, 101, 101.5), (101.5, 101.6, 100.3, 100.5),
            (100.5, 102, 100.1, 101.8),  # Wed: bullish engulfing tagging 100
            (101.8, 102.5, 101.5, 102), (102, 102.6, 101.8, 102.5),
            (102.5, 103, 102, 102.8), (102.8, 103.2, 102.5, 103)]
    bars = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    bars.insert(0, "ts", ts)
    bars["volume"] = 1.0
    pos = _run("MTF-WKENG-001", bars)
    assert (pos.iloc[:7] == 0).all()
    assert pos.iloc[7] == 1


def test_micro_pullback_scalp_buys_the_first_pullback_in_a_run():
    close = np.concatenate([np.full(20, 100.0), [100.5, 101, 101.5, 102, 101.8, 102.3]])
    ts = pd.date_range("2025-03-03 14:30", periods=len(close), freq="1min", tz="UTC")
    bars = _bars(ts, close, wick=0.05)
    pos = _run("SC-MICRO-001", bars)
    assert pos.iloc[-1] == 1 and (pos.iloc[:-1] == 0).all()
    assert (_run("SC-MICRO-001", bars, allow_short=False) >= 0).all()
