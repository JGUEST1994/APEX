"""Batch 6: H20 gap-and-go, and the synthetic provider's optional overnight gaps. (Look-ahead
and "trades on some data" checks run in test_strategies_research_api.py with every
registered strategy; BO-GAP-001 trades on the gapped 5m dataset there.)"""

import numpy as np
import pandas as pd

from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.strategies import indicators as ind
from apex.strategies.registry import all_strategies
from tests.conftest import utc

SPY = by_symbol()["SPY"]


def _week():
    """Five quiet RTH sessions at 100 (first-bar volume 1000, others 500)."""
    ts = SyntheticProvider().fetch_bars(SPY, Timeframe.M5, utc(2025, 3, 3), utc(2025, 3, 8))["ts"]
    key = ind.session_keys(pd.DataFrame({"ts": ts}), "us_equity_rth")
    n = key.groupby(key).cumcount()
    bars = pd.DataFrame({"ts": ts, "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0,
                         "volume": np.where(n == 0, 1000.0, 500.0)})
    last = (key == key.iloc[-1]).to_numpy()
    return bars, last, n.to_numpy()


def _gap_day(bars, last, n, gap=0.03, first_vol=2000.0, break_at=2):
    """Last session opens ``gap`` away, its opening bar spans +/- 0.5, and bar ``break_at``
    closes 1.0 beyond the opening bar in the gap direction (then holds there)."""
    b = bars.copy()
    px = 100.0 * (1 + gap)
    side = 1.0 if gap > 0 else -1.0
    day = np.flatnonzero(last)
    b.loc[day, ["open", "close"]] = px
    b.loc[day, "high"], b.loc[day, "low"] = px + 0.1, px - 0.1
    b.loc[day[0], ["high", "low"]] = px + 0.5, px - 0.5
    b.loc[day[0], "volume"] = first_vol
    after = day[break_at:]
    b.loc[after, ["open", "close"]] = px + side * 1.0
    b.loc[after, "high"], b.loc[after, "low"] = px + side + 0.1, px + side - 0.1
    return b, day


def _run(bars, **over):
    s = all_strategies()["BO-GAP-001"]()
    return s.target_position(bars, s.params(over))


def test_gap_up_on_volume_buys_the_break_and_is_flat_before_the_close():
    bars, last, n = _week()
    b, day = _gap_day(bars, last, n)
    pos = _run(b)
    assert (pos[~last] == 0).all()
    assert pos[day[2]] == 1.0 and (pos[day[:2]] == 0).all()
    assert (pos[ind.bars_left_in_session(b, "us_equity_rth") < 1] == 0).all()


def test_gap_down_sells_the_break_and_long_only_skips_it():
    bars, last, n = _week()
    b, day = _gap_day(bars, last, n, gap=-0.03)
    assert _run(b)[day[2]] == -1.0
    assert (_run(b, allow_short=False) == 0).all()


def test_small_gap_light_volume_or_late_break_do_not_trade():
    bars, last, n = _week()
    assert (_run(_gap_day(bars, last, n, gap=0.01)[0]) == 0).all()
    assert (_run(_gap_day(bars, last, n, first_vol=900.0)[0]) == 0).all()
    assert (_run(_gap_day(bars, last, n, break_at=10)[0]) == 0).all()
    assert (_run(_gap_day(bars, last, n, break_at=10)[0], entry_bars=12) != 0).any()


def test_stop_at_the_opening_bar_low():
    bars, last, n = _week()
    b, day = _gap_day(bars, last, n)
    b.loc[day[5]:, ["open", "close"]] = 102.0  # back below the opening bar's low (102.5)
    pos = _run(b)
    assert pos[day[2]] == 1.0 and (pos[day[5]:] == 0).all()


def test_synthetic_gaps_only_after_breaks_and_default_unchanged():
    start, end = utc(2025, 3, 3), utc(2025, 3, 15)
    plain = SyntheticProvider().fetch_bars(SPY, Timeframe.M5, start, end)
    gapped = SyntheticProvider(gap_vol=0.03).fetch_bars(SPY, Timeframe.M5, start, end)
    assert (plain["open"].iloc[1:].to_numpy() == plain["close"].iloc[:-1].to_numpy()).all()
    jump = (gapped["open"] / gapped["close"].shift(1) - 1).abs() > 1e-12
    new_session = gapped["ts"].diff() > pd.Timedelta(minutes=5)
    assert jump.any() and not (jump & ~new_session).any()
    assert (gapped["high"] >= gapped[["open", "close"]].max(axis=1)).all()
    crypto = SyntheticProvider(gap_vol=0.03).fetch_bars(by_symbol()["BTCUSDT"], Timeframe.H1,
                                                        start, end)
    assert np.allclose(crypto["open"].iloc[1:].to_numpy(), crypto["close"].iloc[:-1].to_numpy())
