"""Strategy batch 4: FX session reversion and sweeps (H15, H32), liquidity and structure
(H34, H36), multi-timeframe (H46, H47) and a 1-minute scalp (H51).

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

FX = (AssetClass.FOREX,)


def _asia(bars: pd.DataFrame, start: int, end: int):
    """Running Asia-session (UTC hours [start, end)) high, low, first open and last close per
    UTC day, each known only from bars already closed."""
    ts = bars["ts"]
    hour, day = ts.dt.hour, ts.dt.floor("D")
    inside = (hour >= start) & (hour < end)
    g = lambda s: s.where(inside).groupby(day)  # noqa: E731
    hi = g(bars["high"]).cummax().groupby(day).ffill()
    lo = g(bars["low"]).cummin().groupby(day).ffill()
    first_open = g(bars["open"]).transform("first")
    first_open = first_open.where(inside.groupby(day).cummax())  # known once Asia began
    last_close = g(bars["close"]).ffill().groupby(day).ffill()
    return hour, day, hi, lo, first_open, last_close


def _swings(bars: pd.DataFrame, k: int):
    """Per bar, the last two confirmed swing highs and lows (fractals of k bars each side,
    confirmed k bars later). NaN until they exist."""
    h, lo = bars["high"].to_numpy(float), bars["low"].to_numpy(float)
    n = len(bars)
    sh = np.full((n, 2), np.nan)
    sl = np.full((n, 2), np.nan)
    hs: list[float] = []
    ls: list[float] = []
    for i in range(n):
        j = i - k
        if j - k >= 0:
            if h[j] == h[j - k:j + k + 1].max():
                hs.append(h[j])
            if lo[j] == lo[j - k:j + k + 1].min():
                ls.append(lo[j])
        if hs:
            sh[i] = [hs[-1], hs[-2] if len(hs) > 1 else np.nan]
        if ls:
            sl[i] = [ls[-1], ls[-2] if len(ls) > 1 else np.nan]
    return sh, sl


@register
class AsiaExtensionReversion(Strategy):
    meta = _meta("MR-ASIA-001", "FX overnight (Asia) extension reversion", "mean_reversion",
                 "When the Asia session's range is unusually wide, fade its direction at the "
                 "London open, back toward the Asia midpoint.",
                 "first bar at `range_end` UTC: Asia range > `mult` x its `avg_days`-day "
                 "average -> trade against the Asia move", "`exit_hour` UTC",
                 "`stop_mult` x Asia range", "Asia midpoint",
                 {"range_start": 0, "range_end": 7, "exit_hour": 10, "avg_days": 5,
                  "mult": 1.3, "stop_mult": 2.0, "allow_short": True},
                 tfs=(Timeframe.M15, Timeframe.M30, Timeframe.H1), assets=FX,
                 regimes=("range",))

    def target_position(self, bars, p):
        hour, day, hi, lo, op, cl = _asia(bars, p["range_start"], p["range_end"])
        width = hi - lo
        daily = width.groupby(day).last().dropna()  # complete only for past days
        avg = daily.shift(1).rolling(p["avg_days"], min_periods=p["avg_days"]).mean()
        avg = day.map(avg)
        start = (hour == p["range_end"]) & (hour.shift(1) != p["range_end"])
        wide = width > p["mult"] * avg
        move = np.sign(cl - op)
        sig = pd.Series(np.where(start & wide, -move, 0.0), index=bars.index).fillna(0.0)
        if not p["allow_short"]:
            sig = sig.clip(lower=0)
        c = bars["close"]
        stop = c - sig.where(sig != 0, 1.0) * p["stop_mult"] * width
        window = (hour >= p["range_end"]) & (hour < p["exit_hour"])
        return _trade(bars, sig, stop, (hi + lo) / 2, flat=~window)


@register
class AsiaRangeSweep(Strategy):
    meta = _meta("LIQ-ASIA-001", "Asian range sweep (FX)", "liquidity_sweep",
                 "London often runs the stops beyond one side of the Asia range and then "
                 "reverses: fade a bar that trades through an Asia extreme and closes back "
                 "inside, once per day, toward the opposite edge.",
                 "high > Asia high and close < Asia high (short); mirror long",
                 "`flat_hour` UTC", "sweep bar extreme", "opposite Asia edge",
                 {"range_start": 0, "range_end": 7, "flat_hour": 16, "allow_short": True},
                 tfs=(Timeframe.M5, Timeframe.M15, Timeframe.M30), assets=FX)

    def target_position(self, bars, p):
        hour, day, hi, lo, _, _ = _asia(bars, p["range_start"], p["range_end"])
        h, low, c = bars["high"], bars["low"], bars["close"]
        window = (hour >= p["range_end"]) & (hour < p["flat_hour"])
        short = window & (h > hi) & (c < hi) & (c > lo)
        long_ = window & (low < lo) & (c > lo) & (c < hi)
        hit = (long_ | (short & p["allow_short"])).astype(int).groupby(day).cumsum()
        first = hit == 1
        sig = pd.Series(np.where(long_ & first, 1.0,
                                 np.where(short & first & p["allow_short"], -1.0, 0.0)),
                        index=bars.index)
        stop = pd.Series(np.where(sig > 0, low, h), index=bars.index)
        target = pd.Series(np.where(sig > 0, hi, lo), index=bars.index)
        return _trade(bars, sig, stop, target, flat=~window)


@register
class EqualHighsRaid(Strategy):
    meta = _meta("LIQ-EQH-001", "Equal highs/lows raid", "liquidity_sweep",
                 "Two swing highs within `tol` ATR of each other mark resting stops; when a "
                 "bar runs them and closes back below, fade it (mirror for equal lows).",
                 "high > equal-highs level and close < it (short); mirror long",
                 "`max_bars` bars", "sweep bar extreme", "`rr` x risk",
                 {"k": 3, "tol": 0.1, "atr_n": 14, "rr": 2.0, "max_bars": 6,
                  "allow_short": True}, tfs=(Timeframe.M15, Timeframe.H1),
                 assets=(AssetClass.CRYPTO, AssetClass.FOREX))

    def target_position(self, bars, p):
        sh, sl = _swings(bars, p["k"])
        a = ind.atr(bars, p["atr_n"]).to_numpy()
        h, low, c = (bars[x].to_numpy(float) for x in ("high", "low", "close"))
        eq_h = np.abs(sh[:, 0] - sh[:, 1]) <= p["tol"] * a
        eq_l = np.abs(sl[:, 0] - sl[:, 1]) <= p["tol"] * a
        lvl_h, lvl_l = np.fmax(sh[:, 0], sh[:, 1]), np.fmin(sl[:, 0], sl[:, 1])
        # the level must be from before this bar (swings lag by k bars, so it is)
        short = eq_h & (h > lvl_h) & (c < lvl_h)
        long_ = eq_l & (low < lvl_l) & (c > lvl_l)
        sig = np.where(long_, 1.0, np.where(short & p["allow_short"], -1.0, 0.0))
        stop = np.where(sig > 0, low, h)
        risk = np.abs(c - stop)
        target = c + np.sign(sig) * p["rr"] * risk
        idx = bars.index
        return _trade(bars, pd.Series(sig, index=idx), pd.Series(stop, index=idx),
                      pd.Series(target, index=idx), max_bars=int(p["max_bars"]))


@register
class ChangeOfCharacter(Strategy):
    meta = _meta("MS-CHOCH-001", "Change of character reversal", "market_structure",
                 "After a run of higher highs and higher lows, the first close below the "
                 "last swing low marks a reversal (mirror after lower highs and lows).",
                 "close < last swing low while the last two swings made HH and HL (short)",
                 "stop or target", "last swing high (short) / low (long)", "`rr` x risk",
                 {"k": 3, "rr": 2.0, "allow_short": True}, tfs=INTRADAY + (Timeframe.H1,),
                 assets=(AssetClass.FOREX, AssetClass.CRYPTO))

    def target_position(self, bars, p):
        sh, sl = _swings(bars, p["k"])
        c = bars["close"].to_numpy(float)
        prev_c = np.concatenate([[np.nan], c[:-1]])
        up = (sh[:, 0] > sh[:, 1]) & (sl[:, 0] > sl[:, 1])
        down = (sh[:, 0] < sh[:, 1]) & (sl[:, 0] < sl[:, 1])
        short = up & (c < sl[:, 0]) & ~(prev_c < sl[:, 0])  # first close through it
        long_ = down & (c > sh[:, 0]) & ~(prev_c > sh[:, 0])
        sig = np.where(long_, 1.0, np.where(short & p["allow_short"], -1.0, 0.0))
        stop = np.where(sig > 0, sl[:, 0], sh[:, 0])
        target = c + np.sign(sig) * p["rr"] * np.abs(c - stop)
        idx = bars.index
        return _trade(bars, pd.Series(sig, index=idx), pd.Series(stop, index=idx),
                      pd.Series(target, index=idx))


@register
class DailyBiasOrb(Strategy):
    meta = _meta("MTF-ORB-001", "Daily bias, intraday opening-range breakout",
                 "multi_timeframe",
                 "Take the opening-range breakout only in the direction of the daily trend "
                 "(previous session close vs the SMA of the last `sma` session closes).",
                 "bias long and close > opening-range high (once per session); mirror short",
                 "session close (flat before the last `flat_bars` bars)", "other side of the "
                 "opening range", "`rr` x risk",
                 {"range_bars": 6, "sma": 10, "rr": 2.0, "calendar": "us_equity_rth",
                  "flat_bars": 1, "allow_short": True},
                 tfs=(Timeframe.M5, Timeframe.M15), assets=(AssetClass.FUTURES,
                                                            AssetClass.EQUITY),
                 regimes=("trending",))

    def target_position(self, bars, p):
        key = ind.session_keys(bars, p["calendar"])
        c = bars["close"]
        closes = c.groupby(key).last()  # a session's close is used only from the next one
        sma = closes.rolling(p["sma"], min_periods=p["sma"]).mean()
        prev_close, prev_sma = key.map(closes.shift(1)), key.map(sma.shift(1))
        bias = np.sign(prev_close - prev_sma).fillna(0.0)
        n = bars.groupby(key).cumcount()
        rb = p["range_bars"]
        in_or = n < rb
        or_hi = bars["high"].where(in_or).groupby(key).cummax().groupby(key).ffill()
        or_lo = bars["low"].where(in_or).groupby(key).cummin().groupby(key).ffill()
        after = n >= rb
        long_ = after & (bias > 0) & (c > or_hi)
        short = after & (bias < 0) & (c < or_lo) & p["allow_short"]
        hit = (long_ | short).astype(int).groupby(key).cumsum() == 1
        sig = pd.Series(np.where(long_ & hit, 1.0, np.where(short & hit, -1.0, 0.0)),
                        index=bars.index)
        stop = pd.Series(np.where(sig > 0, or_lo, or_hi), index=bars.index)
        target = c + sig * p["rr"] * (c - stop).abs()
        new_session = key != key.shift(1)
        pos = _trade(bars, sig, stop, target, flat=new_session)
        return ind.flat_before_close(pos, bars, p["calendar"], p["flat_bars"])


@register
class WeeklyLevelEngulfing(Strategy):
    meta = _meta("MTF-WKENG-001", "Weekly level, daily engulfing trigger", "multi_timeframe",
                 "A daily bullish engulfing bar that tags the prior week's low (within `tol`) "
                 "is a reversal; mirror at the prior week's high.",
                 "bullish engulfing with low <= prior-week low x (1 + tol) (long); mirror",
                 "`max_bars` bars", "pattern low (long) / high (short)",
                 "prior week's high (long) / low (short)",
                 {"tol": 0.002, "max_bars": 5, "allow_short": True}, tfs=(Timeframe.D1,),
                 assets=(AssetClass.FOREX, AssetClass.CRYPTO), regimes=("range",))

    def target_position(self, bars, p):
        ts = bars["ts"]
        week = ts.dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period("W")
        wk = bars.groupby(week).agg(hi=("high", "max"), lo=("low", "min"))
        prev = wk.shift(1)  # each week sees only the previous, complete one
        pwh, pwl = week.map(prev["hi"]), week.map(prev["lo"])
        o, h, lo, c = (bars[x] for x in ("open", "high", "low", "close"))
        po, pc = o.shift(1), c.shift(1)
        bull = (pc < po) & (c > o) & (c > po) & (o <= pc)  # synthetic/24h data: no gap
        bear = (pc > po) & (c < o) & (c < po) & (o >= pc)
        long_ = bull & (lo <= pwl * (1 + p["tol"]))
        short = bear & (h >= pwh * (1 - p["tol"])) & p["allow_short"]
        sig = pd.Series(np.where(long_, 1.0, np.where(short, -1.0, 0.0)), index=bars.index)
        stop = pd.Series(np.where(sig > 0, np.fmin(lo, lo.shift(1)),
                                  np.fmax(h, h.shift(1))), index=bars.index)
        target = pd.Series(np.where(sig > 0, pwh, pwl), index=bars.index)
        return _trade(bars, sig, stop, target, max_bars=int(p["max_bars"]))


@register
class MicroPullbackScalp(Strategy):
    meta = _meta("SC-MICRO-001", "Micro pullback scalp", "scalping",
                 "In a strong short-term trend (`run` rising closes above the `ema` EMA), "
                 "buy the first 1-2 bar pullback that holds the EMA when price takes out the "
                 "previous bar's high; mirror for shorts.",
                 "after `run`+ up bars and a 1-2 bar pullback holding the EMA, close > "
                 "previous high (long)", "stop or target", "pullback low (long)",
                 "`rr` x risk",
                 {"ema": 9, "run": 3, "rr": 1.0, "max_bars": 10, "allow_short": True},
                 tfs=(Timeframe.M1, Timeframe.M5), assets=(AssetClass.FUTURES,
                                                           AssetClass.CRYPTO),
                 regimes=("trending",))

    def target_position(self, bars, p):
        o, h, lo, c = (bars[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        e = ind.ema(bars["close"], p["ema"]).to_numpy()
        n, run = len(bars), int(p["run"])
        sig, stop = np.zeros(n), np.full(n, np.nan)
        for i in range(run + 3, n):
            for pb in (1, 2):  # pullback bars i-pb .. i-1, trend run before them
                j0 = i - pb - run
                if j0 < 1:
                    continue
                trend = np.arange(j0, i - pb)
                pull = np.arange(i - pb, i)
                up = (np.all(c[trend] > c[trend - 1]) and np.all(c[trend] > e[trend])
                      and np.all(c[pull] < c[pull - 1]) and np.all(lo[pull] >= e[pull])
                      and c[i] > h[i - 1])
                dn = (np.all(c[trend] < c[trend - 1]) and np.all(c[trend] < e[trend])
                      and np.all(c[pull] > c[pull - 1]) and np.all(h[pull] <= e[pull])
                      and c[i] < lo[i - 1])
                if up:
                    sig[i], stop[i] = 1.0, lo[pull].min()
                    break
                if dn and p["allow_short"]:
                    sig[i], stop[i] = -1.0, h[pull].max()
                    break
        target = c + sig * p["rr"] * np.abs(c - stop)
        idx = bars.index
        return _trade(bars, pd.Series(sig, index=idx), pd.Series(stop, index=idx),
                      pd.Series(target, index=idx), max_bars=int(p["max_bars"]))
