"""Batch 5: bar-approximated volume profile, H09 52-week high, H25 value area rule, H26 POC
magnet. (Look-ahead and "trades on some data" checks for these run in
test_strategies_research_api.py with every registered strategy.)"""

import numpy as np
import pandas as pd

from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.strategies import indicators as ind
from apex.strategies.registry import all_strategies
from tests.conftest import utc

S = all_strategies()


def _run(key, bars, **over):
    s = S[key]()
    return s.target_position(bars, s.params(over))


def _ohlc(ts, rows, volume):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "ts", ts)
    df["volume"] = volume
    return df


def test_volume_profile_finds_the_busiest_price_and_a_70pct_value_area():
    ts = pd.date_range("2025-02-03 14:30", periods=5, freq="30min", tz="UTC")
    rows = [(100, 110, 100, 105), (104, 105, 104, 104.5), (104.5, 105, 104, 104.2),
            (104.2, 105, 104, 104.8), (104.8, 104.8, 104.8, 104.8)]  # last bar: no range
    poc, val, vah = ind.volume_profile(_ohlc(ts, rows, [10, 100, 100, 100, 50]), bins=10)
    assert 104 <= poc <= 105
    assert val <= 104 and vah >= 105 and vah - val < 10  # most volume sits in 104-105
    assert all(np.isnan(x) for x in ind.volume_profile(_ohlc(ts, rows, 0.0)))


def test_prior_session_profile_uses_only_the_previous_session():
    bars = SyntheticProvider(annual_vol=0.4).fetch_bars(by_symbol()["SPY"], Timeframe.M5,
                                                        utc(2025, 3, 3), utc(2025, 3, 6))
    prof = ind.prior_session_profile(bars, "us_equity_rth")
    key = ind.session_keys(bars, "us_equity_rth")
    days = sorted(key.unique())
    assert prof[key == days[0]].isna().all().all()
    expect = ind.volume_profile(bars[key == days[0]])
    got = prof[key == days[1]].iloc[0]
    assert np.allclose([got["poc"], got["val"], got["vah"]], expect)
    assert (got["val"] <= got["poc"]) and (got["poc"] <= got["vah"])


def test_52_week_high_buys_near_the_high_and_trails_10pct():
    close = np.concatenate([np.linspace(100, 90, 30), np.linspace(90, 120, 30),
                            np.linspace(120, 100, 20)])
    ts = pd.date_range("2024-01-01", periods=len(close), freq="D", tz="UTC")
    bars = pd.DataFrame({"ts": ts, "open": close, "high": close, "low": close,
                         "close": close, "volume": 1.0})
    pos = _run("MOM-52WH-001", bars, lookback=20)
    assert (pos[:20] == 0).all()  # no 20-bar history yet / falling
    i_peak = 59
    assert pos.iloc[i_peak] == 1
    out = int(np.flatnonzero(close[i_peak:] <= 0.9 * 120)[0]) + i_peak
    assert (pos.iloc[i_peak:out] == 1).all() and pos.iloc[out] == 0


def test_value_area_rule_shorts_after_two_closes_back_inside():
    ts1 = pd.date_range("2025-02-03 14:30", periods=13, freq="30min", tz="UTC")  # Mon RTH
    ts2 = pd.date_range("2025-02-04 14:30", periods=13, freq="30min", tz="UTC")  # Tue RTH
    day1 = [(100, 101, 99, 100)] * 3 + [(100, 100.5, 99.5, 100)] * 10  # busy 99.5-100.5
    day2 = [(103, 103.5, 102.5, 103), (103, 103, 101.2, 101.2), (101.2, 101.2, 99.9, 100),
            (100, 100.1, 99.8, 99.9)] + [(99.9, 99.9, 98.9, 99)] * 9
    bars = pd.concat([_ohlc(ts1, day1, 100.0), _ohlc(ts2, day2, 100.0)], ignore_index=True)
    prof = ind.prior_session_profile(bars, "us_equity_rth")
    val, vah = prof["val"].iloc[13], prof["vah"].iloc[13]
    assert 99.5 <= val < 99.9 and 100 < vah <= 100.5  # day 2 opened above it
    pos = _run("VP-VA80-001", bars)
    assert (pos[:16] == 0).all()
    assert pos.iloc[16] == -1  # second close inside (bars 15 and 16)
    assert (pos.iloc[17:] == 0).all()  # 99 is below VAL: target reached
    assert (_run("VP-VA80-001", bars, allow_short=False) == 0).all()


def test_poc_magnet_trades_toward_the_prior_poc_and_is_flat_at_the_close():
    bars = SyntheticProvider(annual_vol=0.6).fetch_bars(by_symbol()["SPY"], Timeframe.M5,
                                                        utc(2025, 3, 3), utc(2025, 3, 14))
    pos = _run("VP-POC-001", bars)
    poc = ind.prior_session_profile(bars, "us_equity_rth")["poc"]
    entries = (pos != 0) & (pos != pos.shift(1))
    assert entries.any()
    c = bars["close"]
    assert (c[entries & (pos > 0)] < poc[entries & (pos > 0)]).all()
    assert (c[entries & (pos < 0)] > poc[entries & (pos < 0)]).all()
    assert (pos[ind.bars_left_in_session(bars, "us_equity_rth") < 1] == 0).all()


def _hvn_day1():
    ts = pd.date_range("2025-02-03 14:30", periods=78, freq="5min", tz="UTC")
    rows = ([(100, 100.1, 99.9, 100)] * 30 + [(105, 105.1, 104.9, 105)] * 30
            + [(100, 105, 100, 105), (105, 105, 100, 100)] * 9)
    vol = [1000.0] * 60 + [10.0] * 18
    return _ohlc(ts, rows, vol)


def test_high_volume_nodes_are_the_busy_prices():
    nodes = ind.high_volume_nodes(_hvn_day1())
    assert len(nodes) == 2
    assert abs(nodes[0] - 100) < 0.2 and abs(nodes[1] - 105) < 0.2
    flat = _ohlc(pd.date_range("2025-02-03", periods=3, freq="5min", tz="UTC"),
                 [(1, 1, 1, 1)] * 3, 1.0)
    assert len(ind.high_volume_nodes(flat)) == 0


def test_hvn_rejection_buys_a_wick_off_the_prior_node_and_targets_the_next():
    ts2 = pd.date_range("2025-02-04 14:30", periods=78, freq="5min", tz="UTC")
    rows = ([(100.5, 100.55, 100.45, 100.5)] * 2 + [(100.5, 100.6, 99.95, 100.6)]
            + [(100.7, 100.75, 100.65, 100.7)] * 75)
    bars = pd.concat([_hvn_day1(), _ohlc(ts2, rows, 100.0)], ignore_index=True)
    pos = _run("VP-HVN-001", bars)
    i = 78 + 2
    assert (pos[:i] == 0).all()
    assert (pos.iloc[i:-2] == 1).all() and (pos.iloc[-2:] == 0).all()  # flat by the close
    assert (_run("VP-HVN-001", bars, wick=10.0) == 0).all()  # wick too small: no trade
