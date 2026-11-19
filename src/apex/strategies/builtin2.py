"""Second batch of OHLCV strategy hypotheses (Phase 4, batch 2).

Every strategy here is an UNVALIDATED hypothesis with status ``experimental``. Rules follow
the matching entries (H03, H04, H13, H14, H17, H18, H19, H22, H24, H33, H37, H43) in
docs/STRATEGY_HYPOTHESES.md. Signals are evaluated on bar closes; stops, targets and
time exits are checked on closes too, and the engine fills at the next bar's open.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex.domain.market import AssetClass, Timeframe
from apex.strategies import indicators as ind
from apex.strategies.base import Strategy
from apex.strategies.builtin import INTRADAY, SWING, _meta
from apex.strategies.registry import register

DAILY = (Timeframe.D1,)
NAN = float("nan")


def _trade(bars: pd.DataFrame, sig: pd.Series, stop: pd.Series, target: pd.Series | None = None,
           max_bars: int | None = None, exit_long: pd.Series | None = None,
           exit_short: pd.Series | None = None, flat: pd.Series | None = None) -> pd.Series:
    """Generic causal trade state machine.

    ``sig`` is +1/-1/0 per bar. On entry the stop (and optional target) from that bar are
    frozen. A position closes when the close crosses the stop or target, after ``max_bars``
    bars, on ``exit_long``/``exit_short``, or whenever ``flat`` is True (and no entry is
    taken on a ``flat`` bar).
    """
    n = len(bars)
    c = bars["close"].to_numpy(float)
    s, st = sig.fillna(0).to_numpy(float), stop.to_numpy(float)
    tg = target.to_numpy(float) if target is not None else np.full(n, NAN)
    xl = exit_long.fillna(False).to_numpy(bool) if exit_long is not None else np.zeros(n, bool)
    xs = exit_short.fillna(False).to_numpy(bool) if exit_short is not None else np.zeros(n, bool)
    fl = flat.fillna(False).to_numpy(bool) if flat is not None else np.zeros(n, bool)
    out = np.zeros(n)
    state, age, stop_px, tgt_px = 0.0, 0, NAN, NAN
    for i in range(n):
        if state != 0:
            age += 1
            hit_stop = (state > 0 and c[i] <= stop_px) or (state < 0 and c[i] >= stop_px)
            hit_tgt = (state > 0 and c[i] >= tgt_px) or (state < 0 and c[i] <= tgt_px)
            timed = max_bars is not None and age >= max_bars
            other = (state > 0 and xl[i]) or (state < 0 and xs[i])
            if hit_stop or hit_tgt or timed or other or fl[i]:
                state = 0.0
        if state == 0 and s[i] != 0 and not fl[i]:
            state, age, stop_px, tgt_px = float(np.sign(s[i])), 0, st[i], tg[i]
        out[i] = state
    return pd.Series(out, index=bars.index)


def _directional(long_: pd.Series, short: pd.Series, allow_short: bool) -> pd.Series:
    sig = long_.astype(float) - (short.astype(float) if allow_short else 0.0)
    return sig.fillna(0.0)


@register
class EmaRibbon(Strategy):
    meta = _meta("TF-EMA-RIB-001", "EMA ribbon alignment", "trend_following",
                 "Ordered EMAs (fast > mid > slow) mark a strong trend; ride it until the "
                 "ordering breaks.",
                 "EMA(fast) > EMA(mid) > EMA(slow) and close > EMA(fast); mirror for short",
                 "EMA ordering breaks", "close beyond EMA(slow)", "none",
                 {"fast": 8, "mid": 21, "slow": 55, "allow_short": True},
                 tfs=(Timeframe.H1, Timeframe.D1), regimes=("trending",))

    def target_position(self, bars, p):
        c = bars["close"]
        f, m, s = ind.ema(c, p["fast"]), ind.ema(c, p["mid"]), ind.ema(c, p["slow"])
        up, dn = (f > m) & (m > s), (f < m) & (m < s)
        sig = _directional(up & (c > f), dn & (c < f), p["allow_short"])
        return _trade(bars, sig, s, exit_long=~up, exit_short=~dn)


@register
class ChandelierTrend(Strategy):
    meta = _meta("TF-CHAND-001", "ATR chandelier trend", "trend_following",
                 "Enter on an `entry`-bar closing breakout; exit on a chandelier stop `mult` "
                 "ATRs below the highest close since entry (mirror for short).",
                 "close > prior `entry`-bar high (long) / < low (short)",
                 "close < highest close since entry - mult x ATR", "chandelier trail",
                 "none", {"entry": 50, "atr_n": 22, "mult": 3.0, "allow_short": True},
                 tfs=DAILY + (Timeframe.H1,), regimes=("trending",))

    def target_position(self, bars, p):
        hi, lo = ind.donchian(bars, p["entry"])
        a = ind.atr(bars, p["atr_n"]).to_numpy()
        c = bars["close"].to_numpy()
        long_ = (bars["close"] > hi).to_numpy()
        short = ((bars["close"] < lo) & p["allow_short"]).to_numpy()
        out = np.zeros(len(c))
        state, ext = 0.0, NAN
        for i in range(len(c)):
            if state > 0:
                ext = max(ext, c[i])
                if c[i] < ext - p["mult"] * a[i]:
                    state = 0.0
            elif state < 0:
                ext = min(ext, c[i])
                if c[i] > ext + p["mult"] * a[i]:
                    state = 0.0
            if state == 0 and not np.isnan(a[i]):
                if long_[i]:
                    state, ext = 1.0, c[i]
                elif short[i]:
                    state, ext = -1.0, c[i]
            out[i] = state
        return pd.Series(out, index=bars.index)


@register
class InternalBarStrength(Strategy):
    meta = _meta("MR-IBS-001", "Internal bar strength reversion", "mean_reversion",
                 "A close near the bar's low predicts a bounce in index ETFs.",
                 "(close - low) / (high - low) < `threshold`",
                 "close > prior bar high, or after `max_bars` bars", "time stop", "prior high",
                 {"threshold": 0.2, "max_bars": 5}, tfs=DAILY,
                 assets=(AssetClass.EQUITY, AssetClass.FUTURES), regimes=("range",))

    def target_position(self, bars, p):
        rng = (bars["high"] - bars["low"]).replace(0, np.nan)
        ibs = (bars["close"] - bars["low"]) / rng
        sig = (ibs < p["threshold"]).astype(float)
        exit_ = bars["close"] > bars["high"].shift(1)
        return _trade(bars, sig, pd.Series(NAN, index=bars.index), max_bars=p["max_bars"],
                      exit_long=exit_)


@register
class ConsecutiveDownDays(Strategy):
    meta = _meta("MR-DOWN-001", "Consecutive down closes in uptrend", "mean_reversion",
                 "`n` or more lower closes while above the long-term SMA tend to reverse.",
                 "`n` consecutive lower closes and close > SMA(`trend`)",
                 "first up close, or after `max_bars` bars", "time stop", "first up close",
                 {"n": 3, "trend": 200, "max_bars": 5}, tfs=DAILY,
                 assets=(AssetClass.EQUITY, AssetClass.FUTURES), regimes=("trending",))

    def target_position(self, bars, p):
        c = bars["close"]
        down = (c < c.shift(1)).astype(int)
        streak = down.rolling(p["n"], min_periods=p["n"]).sum() == p["n"]
        sig = (streak & (c > ind.sma(c, p["trend"]))).astype(float)
        return _trade(bars, sig, pd.Series(NAN, index=bars.index), max_bars=p["max_bars"],
                      exit_long=c > c.shift(1))


def _range_break(bars: pd.DataFrame, setup: pd.Series, p: dict) -> pd.Series:
    """Break of the previous bar's range when that bar met ``setup``; stop at the other
    side, target ``rr`` x risk, time exit after ``max_bars``."""
    ph, pl, c = bars["high"].shift(1), bars["low"].shift(1), bars["close"]
    ok = setup.shift(1, fill_value=False).astype(bool)
    long_, short = ok & (c > ph), ok & (c < pl)
    sig = _directional(long_, short, p["allow_short"])
    stop = pd.Series(np.where(sig > 0, pl, ph), index=bars.index)
    target = c + p["rr"] * (c - stop)
    return _trade(bars, sig, stop, target, max_bars=p["max_bars"])


@register
class InsideBarBreakout(Strategy):
    meta = _meta("BO-INSIDE-001", "Inside-bar breakout", "breakout",
                 "Compression inside the prior bar's range precedes expansion.",
                 "close beyond the inside bar's high (long) / low (short)",
                 "after `max_bars` bars", "other side of the inside bar", "`rr` x risk",
                 {"rr": 2.0, "max_bars": 2, "allow_short": True}, tfs=DAILY + (Timeframe.H1,))

    def target_position(self, bars, p):
        h, lo = bars["high"], bars["low"]
        inside = (h < h.shift(1)) & (lo > lo.shift(1))
        return _range_break(bars, inside, p)


@register
class Nr7Breakout(Strategy):
    meta = _meta("BO-NR7-001", "NR7 breakout", "breakout",
                 "The narrowest range of the last `n` bars precedes range expansion.",
                 "close beyond the NR bar's high (long) / low (short)",
                 "after `max_bars` bars", "other side of the NR bar", "`rr` x risk",
                 {"n": 7, "rr": 2.0, "max_bars": 3, "allow_short": True},
                 tfs=DAILY + (Timeframe.H1,))

    def target_position(self, bars, p):
        rng = bars["high"] - bars["low"]
        nr = rng <= rng.rolling(p["n"], min_periods=p["n"]).min()
        return _range_break(bars, nr, p)


@register
class LondonBreakout(Strategy):
    meta = _meta("BO-LDN-001", "London breakout of the Asian range", "breakout",
                 "The London open breaks the Asian-session range with follow-through.",
                 "first close beyond the `range_start`-`range_end` UTC high/low, once per day",
                 "`flat_hour` UTC", "range midpoint", "`tp_mult` x range",
                 {"range_start": 0, "range_end": 7, "flat_hour": 16, "tp_mult": 1.5,
                  "allow_short": True},
                 tfs=(Timeframe.M15, Timeframe.M30, Timeframe.H1), assets=(AssetClass.FOREX,))

    def target_position(self, bars, p):
        ts = bars["ts"]
        hour, day = ts.dt.hour, ts.dt.floor("D")
        in_range = (hour >= p["range_start"]) & (hour < p["range_end"])
        hi = bars["high"].where(in_range).groupby(day).cummax()
        lo = bars["low"].where(in_range).groupby(day).cummin()
        hi, lo = hi.groupby(day).ffill(), lo.groupby(day).ffill()
        window = (hour >= p["range_end"]) & (hour < p["flat_hour"])
        c = bars["close"]
        long_, short = window & (c > hi), window & (c < lo)
        first = (long_ | (short & p["allow_short"])).astype(int).groupby(day).cumsum() == 1
        sig = _directional(long_ & first, short & first, p["allow_short"])
        mid, width = (hi + lo) / 2, hi - lo
        target = pd.Series(np.where(sig > 0, hi + p["tp_mult"] * width,
                                    lo - p["tp_mult"] * width), index=bars.index)
        return _trade(bars, sig, mid, target, flat=~window)


@register
class VwapTrendPullback(Strategy):
    meta = _meta("VWAP-PB-001", "VWAP trend-day pullback", "vwap",
                 "If every close of the first `morning_bars` bars is above VWAP, buy the first "
                 "touch of VWAP that closes back above it (mirror for short).",
                 "trend morning, then low <= VWAP < close", "session end",
                 "close below VWAP - `atr_mult` ATR", "session close",
                 {"morning_bars": 12, "atr_n": 14, "atr_mult": 1.0,
                  "calendar": "us_equity_rth", "allow_short": True},
                 tfs=(Timeframe.M5, Timeframe.M15), assets=(AssetClass.EQUITY,
                                                            AssetClass.FUTURES),
                 regimes=("trending",))

    def target_position(self, bars, p):
        key = ind.session_keys(bars, p["calendar"])
        vwap = ind.session_vwap(bars, p["calendar"])
        c, n = bars["close"], bars.groupby(key).cumcount()
        morning = n < p["morning_bars"]
        above = (c > vwap) | ~morning
        below = (c < vwap) | ~morning
        all_above = above.astype(int).groupby(key).cummin().astype(bool)
        all_below = below.astype(int).groupby(key).cummin().astype(bool)
        after = ~morning
        long_ = after & all_above & (bars["low"] <= vwap) & (c > vwap)
        short = after & all_below & (bars["high"] >= vwap) & (c < vwap)
        a = ind.atr(bars, p["atr_n"])
        sig = _directional(long_, short, p["allow_short"])
        stop = pd.Series(np.where(sig > 0, vwap - p["atr_mult"] * a,
                                  vwap + p["atr_mult"] * a), index=bars.index)
        return _flat_each_session(bars, key, sig, stop)


def _flat_each_session(bars, key, sig, stop, exit_long=None, exit_short=None, target=None):
    """Run ``_trade`` session by session so no position is carried overnight."""
    parts = []
    for _, idx in bars.groupby(key, sort=False).groups.items():
        sl = bars.loc[idx]
        parts.append(_trade(sl, sig.loc[idx], stop.loc[idx],
                            None if target is None else target.loc[idx],
                            exit_long=None if exit_long is None else exit_long.loc[idx],
                            exit_short=None if exit_short is None else exit_short.loc[idx]))
    return pd.concat(parts).reindex(bars.index).fillna(0.0) if parts else \
        pd.Series(0.0, index=bars.index)


@register
class VwapBandBreakout(Strategy):
    meta = _meta("VWAP-BAND-001", "VWAP band breakout", "vwap",
                 "Two closes beyond the `k`-sigma volume-weighted band signal a trend day.",
                 "`confirm` consecutive closes beyond VWAP +/- k sigma",
                 "close back inside the `exit_k`-sigma band, or session end", "VWAP", "none",
                 {"k": 2.0, "exit_k": 1.0, "confirm": 2, "min_bars": 6,
                  "calendar": "us_equity_rth", "allow_short": True},
                 tfs=(Timeframe.M5, Timeframe.M15), assets=(AssetClass.FUTURES,
                                                            AssetClass.CRYPTO,
                                                            AssetClass.EQUITY),
                 regimes=("trending",))

    def target_position(self, bars, p):
        key = ind.session_keys(bars, p["calendar"])
        tp = (bars["high"] + bars["low"] + bars["close"]) / 3
        v = bars["volume"]
        cv = v.groupby(key).cumsum().replace(0, np.nan)
        vwap = (tp * v).groupby(key).cumsum() / cv
        var = ((tp * tp * v).groupby(key).cumsum() / cv - vwap ** 2).clip(lower=0)
        sd = np.sqrt(var)
        c = bars["close"]
        ready = bars.groupby(key).cumcount() >= p["min_bars"]
        up = (c > vwap + p["k"] * sd) & ready
        dn = (c < vwap - p["k"] * sd) & ready
        cu = up.astype(int).groupby(key).rolling(p["confirm"], min_periods=p["confirm"]).sum()
        cd = dn.astype(int).groupby(key).rolling(p["confirm"], min_periods=p["confirm"]).sum()
        cu = cu.reset_index(level=0, drop=True).reindex(bars.index) == p["confirm"]
        cd = cd.reset_index(level=0, drop=True).reindex(bars.index) == p["confirm"]
        sig = _directional(cu, cd, p["allow_short"])
        return _flat_each_session(bars, key, sig, vwap,
                                  exit_long=c < vwap + p["exit_k"] * sd,
                                  exit_short=c > vwap - p["exit_k"] * sd)


@register
class PriorDaySweep(Strategy):
    meta = _meta("LIQ-PDHL-001", "Prior-session high/low sweep", "liquidity_sweep",
                 "Trades through the prior session's high or low that close back inside are "
                 "failed breakouts: fade them toward VWAP.",
                 "high > prior-session high and close back below (short); mirror for long",
                 "session end", "sweep bar extreme", "session VWAP",
                 {"calendar": "us_equity_rth", "allow_short": True},
                 tfs=INTRADAY, assets=(AssetClass.FUTURES, AssetClass.EQUITY),
                 regimes=("range",))

    def target_position(self, bars, p):
        key = ind.session_keys(bars, p["calendar"])
        vwap = ind.session_vwap(bars, p["calendar"])
        sess = bars.groupby(key, sort=False).agg(hi=("high", "max"), lo=("low", "min"))
        prev = sess.shift(1)  # each session sees only the previous, already complete one
        pdh, pdl = key.map(prev["hi"]), key.map(prev["lo"])
        h, lo, c = bars["high"], bars["low"], bars["close"]
        long_ = (lo < pdl) & (c > pdl) & (c < vwap)
        short = (h > pdh) & (c < pdh) & (c > vwap)
        sig = _directional(long_, short, p["allow_short"])
        stop = pd.Series(np.where(sig > 0, lo, h), index=bars.index)
        return _flat_each_session(bars, key, sig, stop, target=vwap)


@register
class FairValueGap(Strategy):
    meta = _meta("MS-FVG-001", "Fair value gap retest", "market_structure",
                 "Three-bar imbalances (bar t-2 high below bar t low) get revisited; buy the "
                 "retest of the latest bullish gap in an uptrend (mirror for short).",
                 "low re-enters the gap, close holds above its far edge, close > EMA(`trend`)",
                 "close beyond the gap's far edge", "gap far edge", "`rr` x risk",
                 {"trend": 50, "rr": 2.0, "max_age": 30, "allow_short": True},
                 tfs=INTRADAY + SWING, regimes=("trending",))

    def target_position(self, bars, p):
        h, lo, c = (bars[k].to_numpy(float) for k in ("high", "low", "close"))
        e = ind.ema(bars["close"], p["trend"]).to_numpy()
        n = len(c)
        sig, stop, tgt = np.zeros(n), np.full(n, NAN), np.full(n, NAN)
        bull = bear = None  # (bottom, top, bar index)
        for i in range(n):
            if i >= 2 and lo[i] > h[i - 2]:
                bull = (h[i - 2], lo[i], i)
                continue
            if i >= 2 and h[i] < lo[i - 2]:
                bear = (h[i], lo[i - 2], i)
                continue
            if np.isnan(e[i]):
                continue
            if bull and i - bull[2] <= p["max_age"] and c[i] > e[i] \
                    and lo[i] <= bull[1] and c[i] > bull[0]:
                sig[i], stop[i] = 1, bull[0]
                tgt[i] = c[i] + p["rr"] * (c[i] - bull[0])
                bull = None
            elif p["allow_short"] and bear and i - bear[2] <= p["max_age"] and c[i] < e[i] \
                    and h[i] >= bear[0] and c[i] < bear[1]:
                sig[i], stop[i] = -1, bear[1]
                tgt[i] = c[i] - p["rr"] * (bear[1] - c[i])
                bear = None
        ix = bars.index
        return _trade(bars, pd.Series(sig, ix), pd.Series(stop, ix), pd.Series(tgt, ix))


@register
class AtrExpansionFollowThrough(Strategy):
    meta = _meta("VOL-ATRX-001", "ATR expansion day follow-through", "volatility_expansion",
                 "A bar with range > `mult` x ATR closing in its top (bottom) `zone` continues "
                 "for `hold` bars.",
                 "range > mult x prior ATR and close in the top `zone` of the range",
                 "after `hold` bars", "bar midpoint", "time exit",
                 {"atr_n": 14, "mult": 2.0, "zone": 0.2, "hold": 1, "allow_short": True},
                 tfs=DAILY + (Timeframe.H1,), assets=(AssetClass.FUTURES, AssetClass.EQUITY,
                                                      AssetClass.CRYPTO))

    def target_position(self, bars, p):
        h, lo, c = bars["high"], bars["low"], bars["close"]
        rng = h - lo
        big = rng > p["mult"] * ind.atr(bars, p["atr_n"]).shift(1)
        pos = (c - lo) / rng.replace(0, np.nan)
        sig = _directional(big & (pos >= 1 - p["zone"]), big & (pos <= p["zone"]),
                           p["allow_short"])
        return _trade(bars, sig, (h + lo) / 2, max_bars=p["hold"])
