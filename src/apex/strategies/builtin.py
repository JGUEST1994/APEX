"""First implemented strategy hypotheses (Phase 4).

Every strategy here is an UNVALIDATED hypothesis, status ``experimental``. None has passed
walk-forward or out-of-sample testing. Rules come from the public trading literature and
are documented in docs/STRATEGY_HYPOTHESES.md under the same key.

All use OHLCV bars only. Strategies needing order-book or tick data (order flow, volume
profile, footprint) remain specs until that data exists.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex.domain.market import AssetClass, Timeframe
from apex.strategies import indicators as ind
from apex.strategies.base import Strategy, StrategyMeta
from apex.strategies.registry import register

ALL = tuple(AssetClass)
INTRADAY = (Timeframe.M5, Timeframe.M15, Timeframe.M30)
SWING = (Timeframe.H1, Timeframe.D1)


def _meta(key, name, category, description, entry, exit_, stop, tp, params, *,  # noqa: ANN001
          tfs=SWING, assets=ALL, regimes=("any",), version="0.1.0") -> StrategyMeta:
    return StrategyMeta(key=key, version=version, name=name, category=category,
                        description=description, entry_rules=entry, exit_rules=exit_,
                        stop_loss=stop, take_profit=tp,
                        position_sizing="fraction of max position from risk engine",
                        asset_classes=assets, timeframes=tfs, regimes=regimes,
                        default_params=params)


@register
class DonchianBreakout(Strategy):
    meta = _meta("BO-DONCH-001", "Donchian channel breakout", "breakout",
                 "Trend-following channel breakout in the style of the classic turtle rules.",
                 "close > highest high of prior `entry` bars (long); < lowest low (short)",
                 "close crosses the opposite `exit`-bar channel",
                 "exit channel acts as the stop", "none (trend following)",
                 {"entry": 20, "exit": 10, "allow_short": True}, regimes=("trending",))

    def target_position(self, bars, p):
        hi, lo = ind.donchian(bars, p["entry"])
        xhi, xlo = ind.donchian(bars, p["exit"])
        c = bars["close"]
        entry = pd.Series(np.where(c > hi, 1, np.where((c < lo) & p["allow_short"], -1, 0)),
                          index=bars.index, dtype=float)
        out = np.zeros(len(bars))
        state = 0.0
        for i in range(len(bars)):
            if state > 0 and c.iat[i] < xlo.iat[i]:
                state = 0.0
            elif state < 0 and c.iat[i] > xhi.iat[i]:
                state = 0.0
            if entry.iat[i] != 0 and entry.iat[i] != state:
                state = entry.iat[i]
            out[i] = state
        return pd.Series(out, index=bars.index)


@register
class TimeSeriesMomentum(Strategy):
    meta = _meta("MOM-TSMOM-001", "Time-series momentum", "momentum",
                 "Hold the sign of the trailing `lookback`-bar return (Moskowitz, Ooi, "
                 "Pedersen 2012 style), optionally scaled to target volatility.",
                 "sign(close_t / close_{t-lookback} - 1)", "sign flips",
                 "none; volatility scaling limits risk", "none",
                 {"lookback": 60, "vol_target": 0.0, "vol_window": 20, "periods_per_year": 252,
                  "allow_short": True},
                 regimes=("trending",))

    def target_position(self, bars, p):
        r = bars["close"].pct_change(p["lookback"])
        pos = np.sign(r).fillna(0.0)
        if not p["allow_short"]:
            pos = pos.clip(lower=0)
        if p["vol_target"] > 0:
            rv = bars["close"].pct_change().rolling(p["vol_window"]).std()
            ppy = p["periods_per_year"]
            scale = (p["vol_target"] / (rv * np.sqrt(ppy))).clip(upper=1.0)
            pos = (pos * scale).fillna(0.0)
        return pos


@register
class Rsi2MeanReversion(Strategy):
    meta = _meta("MR-RSI2-001", "RSI(2) pullback in uptrend", "mean_reversion",
                 "Buy short-term oversold pullbacks inside a long-term uptrend (Connors-style).",
                 "RSI(2) < `oversold` and close > SMA(`trend`)", "close > SMA(`exit_ma`)",
                 "close < SMA(`trend`) (trend filter fails)", "exit at SMA(`exit_ma`)",
                 {"oversold": 10.0, "trend": 200, "exit_ma": 5}, tfs=(Timeframe.D1,),
                 assets=(AssetClass.EQUITY, AssetClass.FUTURES), regimes=("trending", "range"))

    def target_position(self, bars, p):
        c = bars["close"]
        r, trend, xma = ind.rsi(c, 2), ind.sma(c, p["trend"]), ind.sma(c, p["exit_ma"])
        entry = ((r < p["oversold"]) & (c > trend)).astype(float)
        exit_ = (c > xma) | (c < trend)
        return ind.hold_until(entry, exit_)


@register
class BollingerReversion(Strategy):
    meta = _meta("MR-BB-001", "Bollinger band reversion", "mean_reversion",
                 "Fade closes outside the Bollinger bands; exit at the middle band.",
                 "close < lower band (long) / > upper band (short)", "close crosses middle band",
                 "close beyond `stop_k` standard deviations", "middle band",
                 {"n": 20, "k": 2.0, "stop_k": 3.5, "allow_short": True},
                 tfs=INTRADAY + SWING, regimes=("range",))

    def target_position(self, bars, p):
        c = bars["close"]
        lo, mid, hi = ind.bollinger(c, p["n"], p["k"])
        slo, _, shi = ind.bollinger(c, p["n"], p["stop_k"])
        out = np.zeros(len(bars))
        state = 0.0
        for i in range(len(bars)):
            x = c.iat[i]
            if np.isnan(mid.iat[i]):
                out[i] = 0.0
                continue
            if state > 0 and (x >= mid.iat[i] or x < slo.iat[i]):
                state = 0.0
            elif state < 0 and (x <= mid.iat[i] or x > shi.iat[i]):
                state = 0.0
            if state == 0:
                if slo.iat[i] <= x < lo.iat[i]:
                    state = 1.0
                elif p["allow_short"] and hi.iat[i] < x <= shi.iat[i]:
                    state = -1.0
            out[i] = state
        return pd.Series(out, index=bars.index)


@register
class VwapReversion(Strategy):
    meta = _meta("VWAP-MR-001", "Intraday VWAP reversion", "vwap",
                 "Fade large deviations from the session VWAP, exit at VWAP; hold nothing "
                 "over the session's last `flat_bars` bars or overnight.",
                 "(close - VWAP) / rolling std of that deviation < -k (long) or > k (short)",
                 "touch VWAP", "deviation beyond `stop_k`", "VWAP",
                 {"k": 2.0, "stop_k": 4.0, "window": 30, "calendar": "us_equity_rth",
                  "allow_short": True, "flat_bars": 1},
                 tfs=(Timeframe.M1, Timeframe.M5, Timeframe.M15), regimes=("range",),
                 version="0.2.0")

    def target_position(self, bars, p):
        vwap = ind.session_vwap(bars, p["calendar"])
        dev = bars["close"] - vwap
        z = dev / dev.rolling(p["window"], min_periods=p["window"]).std()
        key = ind.session_keys(bars, p["calendar"])
        out = np.zeros(len(bars))
        state, prev_key = 0.0, None
        for i in range(len(bars)):
            if key.iat[i] != prev_key:
                state, prev_key = 0.0, key.iat[i]  # never carry overnight
            zi = z.iat[i]
            if np.isnan(zi):
                out[i] = state = 0.0
                continue
            if (state > 0 and (zi >= 0 or zi < -p["stop_k"])) or \
               (state < 0 and (zi <= 0 or zi > p["stop_k"])):
                state = 0.0
            if state == 0:
                if -p["stop_k"] < zi < -p["k"]:
                    state = 1.0
                elif p["allow_short"] and p["k"] < zi < p["stop_k"]:
                    state = -1.0
            out[i] = state
        return ind.flat_before_close(pd.Series(out, index=bars.index), bars, p["calendar"],
                                     p["flat_bars"])


@register
class OpeningRangeBreakout(Strategy):
    meta = _meta("BO-ORB-001", "Opening range breakout", "breakout",
                 "Trade the break of the first `range_bars` bars of each session; hold nothing "
                 "over the session's last `flat_bars` bars or overnight.",
                 "close above opening-range high (long) / below low (short), once per session",
                 "session end", "opposite side of the opening range", "none (session close)",
                 {"range_bars": 6, "calendar": "us_equity_rth", "allow_short": True,
                  "flat_bars": 1},
                 tfs=INTRADAY, assets=(AssetClass.EQUITY, AssetClass.FUTURES), version="0.2.0")

    def target_position(self, bars, p):
        key = ind.session_keys(bars, p["calendar"])
        idx_in_session = bars.groupby(key).cumcount()
        g = bars.groupby(key)
        or_hi = g["high"].transform(lambda s: s.iloc[: p["range_bars"]].max()
                                    if len(s) else np.nan)
        or_lo = g["low"].transform(lambda s: s.iloc[: p["range_bars"]].min()
                                   if len(s) else np.nan)
        # opening range is known only once its last bar has closed
        known = idx_in_session >= p["range_bars"] - 1
        out = np.zeros(len(bars))
        state, traded, prev_key = 0.0, False, None
        c = bars["close"]
        for i in range(len(bars)):
            if key.iat[i] != prev_key:
                state, traded, prev_key = 0.0, False, key.iat[i]
            if known.iat[i] and idx_in_session.iat[i] >= p["range_bars"]:
                if state > 0 and c.iat[i] < or_lo.iat[i]:
                    state = 0.0
                elif state < 0 and c.iat[i] > or_hi.iat[i]:
                    state = 0.0
                if not traded and state == 0:
                    if c.iat[i] > or_hi.iat[i]:
                        state, traded = 1.0, True
                    elif p["allow_short"] and c.iat[i] < or_lo.iat[i]:
                        state, traded = -1.0, True
            out[i] = state
        return ind.flat_before_close(pd.Series(out, index=bars.index), bars, p["calendar"],
                                     p["flat_bars"])

    # note: or_hi/or_lo use only the first `range_bars` bars of the session and are used
    # only after those bars have closed, so the computation is causal.


@register
class VolatilitySqueeze(Strategy):
    meta = _meta("VOL-SQZ-001", "Volatility squeeze breakout", "volatility_expansion",
                 "After Bollinger bandwidth falls to its lowest `pct` percentile of the last "
                 "`lookback` bars, trade the first close outside the bands.",
                 "squeeze active within last `memory` bars and close breaks a band",
                 "close crosses the middle band", "middle band", "none",
                 {"n": 20, "k": 2.0, "lookback": 120, "pct": 0.1, "memory": 10},
                 tfs=INTRADAY + SWING)

    def target_position(self, bars, p):
        c = bars["close"]
        lo, mid, hi = ind.bollinger(c, p["n"], p["k"])
        bw = (hi - lo) / mid
        thresh = bw.rolling(p["lookback"], min_periods=p["lookback"]).quantile(p["pct"])
        squeeze = (bw <= thresh).astype(float).rolling(p["memory"], min_periods=1).max() > 0
        entry = pd.Series(np.where(squeeze & (c > hi), 1, np.where(squeeze & (c < lo), -1, 0)),
                          index=bars.index, dtype=float)
        cross_mid = ((c - mid) * (c.shift(1) - mid.shift(1))) <= 0
        return ind.hold_until(entry, cross_mid.fillna(False))


@register
class LiquiditySweepReversal(Strategy):
    meta = _meta("LIQ-SWEEP-001", "Liquidity sweep reversal", "liquidity_sweep",
                 "A bar trades through the prior `n`-bar low (high) where stops cluster, then "
                 "closes back inside: fade the failed breakout.",
                 "low < prior n-bar low and close > that low (long); mirror for short",
                 "after `hold` bars or close beyond the sweep extreme",
                 "sweep bar extreme", "time exit",
                 {"n": 20, "hold": 5, "allow_short": True}, tfs=INTRADAY + SWING)

    def target_position(self, bars, p):
        hi, lo = ind.donchian(bars, p["n"])
        long_sig = (bars["low"] < lo) & (bars["close"] > lo)
        short_sig = (bars["high"] > hi) & (bars["close"] < hi) & p["allow_short"]
        out = np.zeros(len(bars))
        state, age, stop = 0.0, 0, np.nan
        for i in range(len(bars)):
            c = bars["close"].iat[i]
            if state != 0:
                age += 1
                if age >= p["hold"] or (state > 0 and c < stop) or (state < 0 and c > stop):
                    state = 0.0
            if state == 0:
                if long_sig.iat[i]:
                    state, age, stop = 1.0, 0, bars["low"].iat[i]
                elif short_sig.iat[i]:
                    state, age, stop = -1.0, 0, bars["high"].iat[i]
            out[i] = state
        return pd.Series(out, index=bars.index)


@register
class BreakOfStructure(Strategy):
    meta = _meta("MS-BOS-001", "Market structure break", "market_structure",
                 "Track confirmed swing highs/lows (fractals of `k` bars each side). Go long "
                 "when close breaks the last swing high while the last swing low is higher "
                 "than the one before (higher low); mirror for short.",
                 "break of last confirmed swing high with higher-low structure",
                 "close below last confirmed swing low", "last swing low", "none",
                 {"k": 3, "allow_short": True}, tfs=INTRADAY + SWING, regimes=("trending",))

    def target_position(self, bars, p):
        k = p["k"]
        h, lo_ = bars["high"].to_numpy(), bars["low"].to_numpy()
        c = bars["close"].to_numpy()
        out = np.zeros(len(bars))
        swings_h: list[float] = []
        swings_l: list[float] = []
        state = 0.0
        for i in range(len(bars)):
            j = i - k  # a fractal at j is confirmed only once bar j+k has closed
            if j - k >= 0:
                if h[j] == h[j - k : j + k + 1].max():
                    swings_h.append(h[j])
                if lo_[j] == lo_[j - k : j + k + 1].min():
                    swings_l.append(lo_[j])
            if len(swings_h) >= 2 and len(swings_l) >= 2:
                if state > 0 and c[i] < swings_l[-1]:
                    state = 0.0
                elif state < 0 and c[i] > swings_h[-1]:
                    state = 0.0
                if state <= 0 and c[i] > swings_h[-1] and swings_l[-1] > swings_l[-2]:
                    state = 1.0
                elif (state >= 0 and p["allow_short"] and c[i] < swings_l[-1]
                      and swings_h[-1] < swings_h[-2]):
                    state = -1.0
            out[i] = state
        return pd.Series(out, index=bars.index)


@register
class MultiTimeframeTrendPullback(Strategy):
    meta = _meta("MTF-PB-001", "Higher-timeframe trend, lower-timeframe pullback",
                 "multi_timeframe",
                 "Higher-timeframe trend from an SMA over `htf_factor`-bar blocks; enter on "
                 "an RSI pullback on the trading timeframe in the trend direction.",
                 "HTF close > HTF SMA and RSI(`rsi_n`) < `pullback` (long); mirror for short",
                 "RSI crosses back above 50 (long) / below 50 (short), or HTF trend flips",
                 "HTF trend flip", "RSI mean",
                 {"htf_factor": 4, "htf_sma": 50, "rsi_n": 14, "pullback": 35.0,
                  "allow_short": True}, tfs=(Timeframe.M15, Timeframe.H1))

    def target_position(self, bars, p):
        c = bars["close"]
        f = p["htf_factor"]
        # HTF close sampled every f bars (completed blocks only), forward-filled: causal
        block_end = (np.arange(len(c)) + 1) % f == 0
        htf_close = c.where(block_end).ffill()
        htf_sma = htf_close.where(block_end).dropna().rolling(p["htf_sma"]).mean() \
            .reindex(c.index).ffill()
        trend = np.sign(htf_close - htf_sma).fillna(0.0)
        r = ind.rsi(c, p["rsi_n"])
        out = np.zeros(len(c))
        state = 0.0
        for i in range(len(c)):
            t, ri = trend.iat[i], r.iat[i]
            if state > 0 and (t <= 0 or ri > 50):
                state = 0.0
            elif state < 0 and (t >= 0 or ri < 50):
                state = 0.0
            if state == 0 and not np.isnan(ri):
                if t > 0 and ri < p["pullback"]:
                    state = 1.0
                elif t < 0 and p["allow_short"] and ri > 100 - p["pullback"]:
                    state = -1.0
            out[i] = state
        return pd.Series(out, index=c.index)

