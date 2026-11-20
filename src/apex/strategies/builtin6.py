"""Strategy batch 6: H20 gap-and-go.

Same contract as the other batches: causal, positions in [-1, 1], fills at the next open.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex.domain.market import AssetClass, Timeframe
from apex.strategies import indicators as ind
from apex.strategies.base import Strategy
from apex.strategies.builtin import _meta
from apex.strategies.builtin2 import _trade
from apex.strategies.registry import register


@register
class GapAndGo(Strategy):
    meta = _meta("BO-GAP-001", "Gap-and-go", "breakout",
                 "A large opening gap on heavy first-bar volume tends to keep going: buy the "
                 "first break above the opening bar's high after a gap up (mirror for a gap "
                 "down).",
                 "gap from the previous session's close > `gap_pct`, first-bar volume >= "
                 "`vol_mult` x its average over the last `vol_sessions` sessions, then a close "
                 "beyond the first bar's high (gap up) or low (gap down) within `entry_bars` "
                 "bars; once per session",
                 "session close (flat before the last `flat_bars` bars)",
                 "first bar's low (long) / high (short)", "`rr` x risk",
                 {"gap_pct": 0.02, "vol_mult": 1.0, "vol_sessions": 10, "entry_bars": 6,
                  "rr": 2.0, "calendar": "us_equity_rth", "flat_bars": 1,
                  "allow_short": True},
                 tfs=(Timeframe.M5, Timeframe.M15), assets=(AssetClass.EQUITY,))

    def target_position(self, bars, p):
        key = ind.session_keys(bars, p["calendar"])
        o, h, lo, c, v = (bars[x] for x in ("open", "high", "low", "close", "volume"))
        n = bars.groupby(key).cumcount()
        first = n == 0
        # Previous session's last close, opening bar's open/high/low/volume (all known by the
        # end of the opening bar, and only used from the bar after it).
        prev_close = key.map(c.groupby(key).last().shift(1))
        gap = key.map(o.groupby(key).first()) / prev_close - 1.0
        f_hi = h.where(first).groupby(key).transform("first")
        f_lo = lo.where(first).groupby(key).transform("first")
        f_vol = v.groupby(key).first()
        avg = f_vol.shift(1).rolling(int(p["vol_sessions"]), min_periods=3).mean()
        heavy = key.map(f_vol >= p["vol_mult"] * avg).fillna(False).astype(bool)
        window = (n >= 1) & (n <= int(p["entry_bars"])) & heavy
        long_ = window & (gap > p["gap_pct"]) & (c > f_hi)
        short = window & (gap < -p["gap_pct"]) & (c < f_lo) & p["allow_short"]
        hit = (long_ | short).astype(int).groupby(key).cumsum() == 1
        sig = pd.Series(np.where(long_ & hit, 1.0, np.where(short & hit, -1.0, 0.0)),
                        index=bars.index)
        stop = pd.Series(np.where(sig > 0, f_lo, f_hi), index=bars.index)
        target = c + sig * p["rr"] * (c - stop).abs()
        pos = _trade(bars, sig, stop, target, flat=key != key.shift(1))
        return ind.flat_before_close(pos, bars, p["calendar"], p["flat_bars"])
