"""Hand-built bar sequences checking the rules of the Phase 4 batch-2 strategies."""

import numpy as np
import pandas as pd

from apex.strategies.builtin2 import (
    AtrExpansionFollowThrough,
    ConsecutiveDownDays,
    InsideBarBreakout,
    InternalBarStrength,
    LondonBreakout,
    PriorDaySweep,
    _trade,
)


def _bars(rows, start="2025-01-06", freq="1D"):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["volume"] = 1000.0
    df.insert(0, "ts", pd.date_range(start, periods=len(df), freq=freq, tz="UTC"))
    return df


def test_trade_state_machine_stop_target_time():
    b = _bars([(10, 10, 10, 10), (10, 10, 10, 10), (10, 10, 10, 9), (10, 10, 10, 10),
               (10, 10, 10, 12), (10, 10, 10, 10)])
    sig = pd.Series([1, 0, 0, 1, 0, 0], dtype=float)
    stop = pd.Series(9.5, index=b.index)
    tgt = pd.Series(11.5, index=b.index)
    # long at 0, stopped at 2 (close 9 <= 9.5), re-entered at 3, target hit at 4
    assert _trade(b, sig, stop, tgt).tolist() == [1, 1, 0, 1, 0, 0]
    # time exit after 1 bar
    assert _trade(b, sig, pd.Series(np.nan, index=b.index), max_bars=1).tolist() == \
        [1, 0, 0, 1, 0, 0]


def test_inside_bar_breakout_long_then_stop():
    b = _bars([(10, 12, 8, 10),      # mother bar
               (10, 11, 9, 10),      # inside bar
               (10, 11.5, 10, 11.2),  # close above inside high 11 -> long, stop 9
               (11, 11, 8.5, 8.8)])  # close below 9 -> stopped
    pos = InsideBarBreakout().target_position(b, InsideBarBreakout().params())
    assert pos.tolist() == [0, 0, 1, 0]


def test_internal_bar_strength_exits_above_prior_high():
    b = _bars([(10, 11, 9, 9.2),   # IBS 0.1 -> long
               (9.2, 9.8, 9, 9.5),  # hold
               (9.5, 10.5, 9.4, 10.2)])  # close > prior high 9.8 -> exit
    pos = InternalBarStrength().target_position(b, InternalBarStrength().params())
    assert pos.tolist() == [1, 1, 0]


def test_consecutive_down_days_requires_uptrend():
    up = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)]
    dips = [(109, 109, 107, 108.5), (108.5, 108.6, 107, 108), (108, 108.1, 107, 107.5),
            (107.5, 109, 107.4, 108.8)]
    b = _bars(up + dips)
    s = ConsecutiveDownDays()
    pos = s.target_position(b, s.params({"trend": 10}))
    assert pos.iloc[12] == 1 and pos.iloc[13] == 0  # 3rd down close enters, up close exits
    assert s.target_position(b, s.params({"trend": 200})).abs().sum() == 0  # no trend yet


def test_london_breakout_once_per_day_and_flat_at_cutoff():
    rows = [(1.0, 1.002, 0.998, 1.0)] * 28  # 00:00-06:45, range 0.998-1.002
    rows += [(1.0, 1.004, 1.0, 1.003)]       # 07:00 close above range -> long
    rows += [(1.003, 1.004, 1.002, 1.003)] * 35  # holds until 15:45
    rows += [(1.003, 1.004, 1.002, 1.003)] * 4   # 16:00+ flat
    b = _bars(rows, start="2025-01-06 00:00", freq="15min")
    s = LondonBreakout()
    pos = s.target_position(b, s.params())
    assert pos.iloc[:28].abs().sum() == 0
    assert pos.iloc[28] == 1 and pos.iloc[63] == 1
    assert pos.iloc[64:].abs().sum() == 0


def test_prior_day_sweep_short():
    def day(date, rows):
        return _bars(rows, start=f"{date} 14:30", freq="30min")
    d1 = day("2025-01-06", [(100, 101, 99, 100)] * 13)
    d2 = day("2025-01-07", [(100, 100.5, 99.8, 100.2)] * 3
             + [(100.4, 101.5, 100.3, 100.8)]  # trades above PDH 101, closes back below
             + [(100.8, 100.9, 100.0, 100.1)] * 9)
    b = pd.concat([d1, d2], ignore_index=True)
    s = PriorDaySweep()
    pos = s.target_position(b, s.params())
    assert pos.iloc[:13].abs().sum() == 0
    assert pos.iloc[16] == -1  # short on the sweep bar (close above VWAP, below PDH)


def test_atr_expansion_holds_one_bar():
    calm = [(100, 100.5, 99.5, 100)] * 20
    b = _bars(calm + [(100, 104, 99.8, 103.8), (103.8, 104, 103, 103.5), (103.5, 104, 103, 103.6)])
    s = AtrExpansionFollowThrough()
    pos = s.target_position(b, s.params())
    assert pos.iloc[20] == 1 and pos.iloc[21] == 0
