"""Strategy batch 5: 52-week-high momentum (H09) and two volume-profile ideas (H25, H26).

The volume-profile strategies use ``indicators.volume_profile``, which approximates
volume-at-price from bars (each bar's volume spread over its range). The hypotheses ask for
tick-built profiles; results from the bar approximation are a first look only.

Same contract as the other batches: causal, positions in [-1, 1], fills at the next open.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex.domain.market import AssetClass, Timeframe
from apex.strategies import indicators as ind
from apex.strategies.base import Strategy
from apex.strategies.builtin import INTRADAY, _meta
from apex.strategies.builtin2 import _trade
from apex.strategies.registry import register

FUT_EQ = (AssetClass.FUTURES, AssetClass.EQUITY)


@register
class FiftyTwoWeekHigh(Strategy):
    meta = _meta("MOM-52WH-001", "52-week high proximity", "momentum",
                 "Stocks trading near their 52-week high tend to keep drifting up (investors "
                 "anchor on the old high and under-react). Buy near the high, trail a stop.",
                 "close within `near` of the highest high of the last `lookback` bars",
                 "close `trail` below the highest close since entry", "`trail` trailing",
                 "none", {"lookback": 252, "near": 0.02, "trail": 0.10},
                 tfs=(Timeframe.D1,), assets=(AssetClass.EQUITY,), regimes=("trending",))

    def target_position(self, bars, p):
        c = bars["close"].to_numpy(float)
        hi = bars["high"].rolling(p["lookback"], min_periods=p["lookback"]).max().to_numpy()
        near = c >= (1 - p["near"]) * hi
        out = np.zeros(len(c))
        state, peak = 0.0, np.nan
        for i in range(len(c)):
            if state:
                peak = max(peak, c[i])
                if c[i] <= (1 - p["trail"]) * peak:
                    state = 0.0
            if not state and near[i]:
                state, peak = 1.0, c[i]
            out[i] = state
        return pd.Series(out, index=bars.index)


def _session_frame(bars: pd.DataFrame, p: dict):
    key = ind.session_keys(bars, p["calendar"])
    prof = ind.prior_session_profile(bars, p["calendar"], int(p["bins"]), p["value_area"])
    return key, prof, key != key.shift(1)


@register
class ValueAreaRule(Strategy):
    meta = _meta("VP-VA80-001", "Value area 80% rule", "volume_profile",
                 "If a session opens outside yesterday's value area and then spends two bars "
                 "back inside it, price tends to cross the whole value area. Volume profile "
                 "is approximated from bars.",
                 "opened above VAH (below VAL), then `confirm` consecutive closes inside the "
                 "value area -> short (long), once per session", "session close",
                 "close back beyond the edge it entered through", "opposite value-area edge",
                 {"confirm": 2, "bins": 30, "value_area": 0.7, "calendar": "us_equity_rth",
                  "flat_bars": 1, "allow_short": True},
                 tfs=(Timeframe.M15, Timeframe.M30), assets=FUT_EQ, regimes=("range",))

    def target_position(self, bars, p):
        key, prof, new_session = _session_frame(bars, p)
        val, vah = prof["val"], prof["vah"]
        c = bars["close"]
        first_open = bars["open"].groupby(key).transform("first")
        inside = (c > val) & (c < vah)
        n = int(p["confirm"])
        streak = inside.astype(int).groupby(key).rolling(n, min_periods=n).sum()
        streak = streak.reset_index(level=0, drop=True).reindex(bars.index)
        confirmed = streak == n
        long_ = confirmed & (first_open < val)
        short = confirmed & (first_open > vah) & p["allow_short"]
        hit = (long_ | short).astype(int).groupby(key).cumsum() == 1
        sig = pd.Series(np.where(long_ & hit, 1.0, np.where(short & hit, -1.0, 0.0)),
                        index=bars.index)
        stop = pd.Series(np.where(sig > 0, val, vah), index=bars.index)
        target = pd.Series(np.where(sig > 0, vah, val), index=bars.index)
        pos = _trade(bars, sig, stop, target, flat=new_session)
        return ind.flat_before_close(pos, bars, p["calendar"], p["flat_bars"])


@register
class PocMagnet(Strategy):
    meta = _meta("VP-POC-001", "Point of control magnet", "volume_profile",
                 "Price stretched more than `k` ATR from the previous session's point of "
                 "control on fading volume tends to drift back to it. Volume profile is "
                 "approximated from bars.",
                 "|close - prior POC| > `k` x ATR and volume < its `vol_n`-bar average -> "
                 "trade toward the POC", "touch POC or session close",
                 "1 ATR beyond the entry", "prior session POC",
                 {"k": 1.0, "atr_n": 14, "vol_n": 20, "bins": 30, "value_area": 0.7,
                  "calendar": "us_equity_rth", "flat_bars": 1, "allow_short": True},
                 tfs=INTRADAY, assets=FUT_EQ, regimes=("range",))

    def target_position(self, bars, p):
        _, prof, new_session = _session_frame(bars, p)
        poc = prof["poc"]
        c, v = bars["close"], bars["volume"]
        a = ind.atr(bars, p["atr_n"])
        fading = v < ind.sma(v, p["vol_n"])
        dist = c - poc
        long_ = (dist < -p["k"] * a) & fading
        short = (dist > p["k"] * a) & fading & p["allow_short"]
        sig = pd.Series(np.where(long_, 1.0, np.where(short, -1.0, 0.0)), index=bars.index)
        stop = c - sig * a
        pos = _trade(bars, sig, stop, poc, flat=new_session)
        return ind.flat_before_close(pos, bars, p["calendar"], p["flat_bars"])


@register
class HvnRejection(Strategy):
    meta = _meta("VP-HVN-001", "High-volume node rejection", "volume_profile",
                 "Prices where the previous session traded heavily (high-volume nodes) act "
                 "as support and resistance: buy a bar that dips into a node and closes back "
                 "above it with a long lower wick (mirror for shorts). Volume profile is "
                 "approximated from bars.",
                 "low <= node < close, lower wick >= `wick` x body (long); mirror short",
                 "next node or session close", "`stop_atr` ATR beyond the node",
                 "next high-volume node in the trade's direction (else `rr` x risk)",
                 {"bins": 30, "node_mult": 1.5, "wick": 1.5, "stop_atr": 0.5, "atr_n": 14,
                  "rr": 2.0, "calendar": "us_equity_rth", "flat_bars": 1,
                  "allow_short": True},
                 tfs=INTRADAY, assets=FUT_EQ, regimes=("any",))

    def target_position(self, bars, p):
        key = ind.session_keys(bars, p["calendar"])
        groups = list(bars.groupby(key, sort=True).groups.items())
        o, h, lo, c = (bars[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        a = ind.atr(bars, p["atr_n"]).to_numpy()
        body = np.abs(c - o)
        low_wick, up_wick = np.minimum(o, c) - lo, h - np.maximum(o, c)
        n = len(bars)
        sig, stop, target = np.zeros(n), np.full(n, np.nan), np.full(n, np.nan)
        pos_of = {ix: i for i, ix in enumerate(bars.index)}
        for (_, prev_ix), (_, ix) in zip(groups, groups[1:], strict=False):
            nodes = ind.high_volume_nodes(bars.loc[prev_ix], int(p["bins"]), p["node_mult"])
            if not len(nodes):
                continue
            for i in (pos_of[j] for j in ix):
                if not np.isfinite(a[i]):
                    continue
                hit_l = nodes[(nodes >= lo[i]) & (nodes < c[i])]
                if len(hit_l) and low_wick[i] >= p["wick"] * max(body[i], 1e-12):
                    lvl = hit_l.min()
                    above = nodes[nodes > c[i]]
                    sig[i], stop[i] = 1.0, lvl - p["stop_atr"] * a[i]
                    target[i] = (above.min() if len(above)
                                 else c[i] + p["rr"] * (c[i] - stop[i]))
                    continue
                hit_s = nodes[(nodes <= h[i]) & (nodes > c[i])]
                if (p["allow_short"] and len(hit_s)
                        and up_wick[i] >= p["wick"] * max(body[i], 1e-12)):
                    lvl = hit_s.max()
                    below = nodes[nodes < c[i]]
                    sig[i], stop[i] = -1.0, lvl + p["stop_atr"] * a[i]
                    target[i] = (below.max() if len(below)
                                 else c[i] - p["rr"] * (stop[i] - c[i]))
        idx = bars.index
        pos = _trade(bars, pd.Series(sig, index=idx), pd.Series(stop, index=idx),
                     pd.Series(target, index=idx), flat=key != key.shift(1))
        return ind.flat_before_close(pos, bars, p["calendar"], p["flat_bars"])
