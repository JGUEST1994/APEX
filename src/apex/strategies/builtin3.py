"""Strategy batch 3: intraday momentum (H08), crypto weekend momentum (H10), volatility-regime
switch (H44), HMM regime gating (H49) and round-number fades (H52).

Same contract as the other batches: causal, positions in [-1, 1], fills at the next open.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex.domain.market import AssetClass, Timeframe
from apex.strategies import indicators as ind
from apex.strategies.base import Strategy
from apex.strategies.builtin import (
    BollingerReversion,
    Rsi2MeanReversion,
    TimeSeriesMomentum,
    _meta,
)
from apex.strategies.builtin2 import _trade
from apex.strategies.reference import SmaCrossover
from apex.strategies.registry import register


def _defaults(cls: type[Strategy]) -> dict:
    return dict(cls.meta.default_params)


@register
class IntradayMomentum(Strategy):
    meta = _meta("MOM-ID30-001", "Intraday momentum (first half hour)", "momentum",
                 "The return from the previous close to the end of the first half hour "
                 "predicts the last half hour (Gao, Han, Li and Zhou 2018). Hold its sign "
                 "over the session's final `last_minutes`, flat by the close.",
                 "sign of previous close -> first `first_minutes` return, entered so the "
                 "position is held over the last `last_minutes`", "session close",
                 "none", "session close",
                 {"first_minutes": 30, "last_minutes": 30, "min_move": 0.0,
                  "calendar": "us_equity_rth", "allow_short": True},
                 tfs=(Timeframe.M5, Timeframe.M15, Timeframe.M30),
                 assets=(AssetClass.EQUITY, AssetClass.FUTURES))

    def target_position(self, bars, p):
        key = ind.session_keys(bars, p["calendar"])
        left = ind.bars_left_in_session(bars, p["calendar"])
        ts = pd.DatetimeIndex(bars["ts"])
        step = ts.to_series().diff().where(lambda d: d > pd.Timedelta(0)).cummin()
        step = step.to_numpy()
        c = bars["close"].to_numpy(float)
        k = key.to_numpy()
        out = np.zeros(len(bars))
        prev_close, cur_key, start_i, first_ret, last_close = np.nan, None, 0, np.nan, np.nan
        for i in range(len(bars)):
            if k[i] != cur_key:
                prev_close, cur_key, start_i, first_ret = last_close, k[i], i, np.nan
            last_close = c[i]
            st = step[i]
            if pd.isna(st) or left.iat[i] >= 10_000:
                continue
            st = pd.Timedelta(st)
            elapsed = (i - start_i + 1) * st
            if np.isnan(first_ret) and elapsed >= pd.Timedelta(minutes=p["first_minutes"]):
                base = prev_close if np.isfinite(prev_close) else bars["open"].iat[start_i]
                first_ret = c[i] / base - 1
            n_last = max(1, int(pd.Timedelta(minutes=p["last_minutes"]) / st))
            if np.isfinite(first_ret) and 1 <= left.iat[i] <= n_last \
                    and abs(first_ret) > p["min_move"]:
                side = np.sign(first_ret)
                out[i] = side if (side > 0 or p["allow_short"]) else 0.0
        return pd.Series(out, index=bars.index)


@register
class CryptoWeekendMomentum(Strategy):
    meta = _meta("MOM-WKND-001", "Crypto weekend momentum", "momentum",
                 "Thin weekend liquidity extends moves started into Friday evening: hold the "
                 "sign of the 24-hour return at Friday `entry_hour` UTC until Monday 00:00 UTC.",
                 "first bar at/after Friday `entry_hour`:00 UTC: sign of its 24h return",
                 "Monday 00:00 UTC", "`atr_mult` ATR from entry", "time exit",
                 {"entry_hour": 20, "atr_n": 24, "atr_mult": 2.0, "allow_short": True},
                 tfs=(Timeframe.M15, Timeframe.M30, Timeframe.H1), assets=(AssetClass.CRYPTO,))

    def target_position(self, bars, p):
        ts = pd.DatetimeIndex(bars["ts"]).tz_convert("UTC")
        wd, hr = ts.day_of_week, ts.hour
        window = pd.Series(((wd == 4) & (hr >= p["entry_hour"])) | (wd == 5) | (wd == 6),
                           index=bars.index)
        start = window & ~window.shift(1, fill_value=False)
        c = bars["close"]
        by_ts = pd.Series(c.to_numpy(float), index=ts)
        ago = by_ts.asof(ts - pd.Timedelta(hours=24))  # last close at or before t - 24h
        ret = pd.Series(c.to_numpy(float) / np.asarray(ago, float) - 1, index=bars.index)
        sig = pd.Series(np.where(start, np.sign(ret.fillna(0.0)), 0.0), index=bars.index)
        if not p["allow_short"]:
            sig = sig.clip(lower=0)
        a = ind.atr(bars, p["atr_n"])
        stop = pd.Series(np.where(sig >= 0, c - p["atr_mult"] * a, c + p["atr_mult"] * a),
                         index=bars.index)
        return _trade(bars, sig, stop, flat=~window)


@register
class VolatilityRegimeSwitch(Strategy):
    meta = _meta("VOL-SWITCH-001", "Volatility-regime switch", "volatility_expansion",
                 "Run the SMA crossover (H01) while realised volatility is rising and the "
                 "Bollinger fade (H12) while it is falling.",
                 "underlying strategy's entry, chosen by the vol regime",
                 "regime flip or underlying exit", "underlying stops", "none",
                 {"vol_n": 20, "vol_lag": 20}, tfs=(Timeframe.D1, Timeframe.H1),
                 regimes=("any",))

    def target_position(self, bars, p):
        rv = np.log(bars["close"]).diff().rolling(p["vol_n"], min_periods=p["vol_n"]).std()
        rising = rv > rv.shift(p["vol_lag"])
        known = rv.notna() & rv.shift(p["vol_lag"]).notna()
        trend = SmaCrossover().target_position(bars, _defaults(SmaCrossover))
        fade = BollingerReversion().target_position(bars, _defaults(BollingerReversion))
        pos = pd.Series(np.where(rising, trend, fade), index=bars.index)
        return pos.where(known, 0.0).astype(float)


@register
class HmmRegimeGate(Strategy):
    meta = _meta("ML-HMM-001", "HMM regime gating", "machine_learning",
                 "A two-state Gaussian HMM is fitted on the first `train_bars` returns and "
                 "then run forward (filtered, never smoothed). In the calm state follow "
                 "time-series momentum (H06); in the turbulent state use the RSI(2) pullback "
                 "(H11).", "underlying strategy's entry, chosen by the filtered HMM state",
                 "state change or underlying exit", "underlying", "underlying",
                 {"train_bars": 300, "seed": 0}, tfs=(Timeframe.D1,), regimes=("any",))

    def target_position(self, bars, p):
        n = int(p["train_bars"])
        if len(bars) <= n:
            return pd.Series(0.0, index=bars.index)
        from apex.regime.detect import hmm_regime

        state, _ = hmm_regime(bars, train_end=n, n_states=2, seed=int(p["seed"]))
        trend = TimeSeriesMomentum().target_position(bars, _defaults(TimeSeriesMomentum))
        revert = Rsi2MeanReversion().target_position(bars, _defaults(Rsi2MeanReversion))
        pos = pd.Series(np.where(state == 0, trend, revert), index=bars.index)
        pos.iloc[:n] = 0.0  # nothing until the model exists
        return pos.astype(float)


@register
class RoundNumberFade(Strategy):
    meta = _meta("SC-ROUND-001", "Round-number fade", "scalping",
                 "First touches of round numbers (e.g. 1.1000, 50 000) see short-term "
                 "rejection: fade a bar that pokes through a level not touched for `memory` "
                 "bars and closes back on its side.",
                 "high >= level > close after an approach from below (short); mirror long",
                 "`max_bars` bars", "`stop_frac` x level spacing through the level",
                 "`target_frac` x level spacing",
                 {"digits": 2, "memory": 60, "stop_frac": 0.1, "target_frac": 0.2,
                  "max_bars": 5, "allow_short": True},
                 tfs=(Timeframe.M1, Timeframe.M5), assets=(AssetClass.FOREX, AssetClass.CRYPTO),
                 regimes=("range",))

    def target_position(self, bars, p):
        c, h, lo = bars["close"], bars["high"], bars["low"]
        prev = c.shift(1)
        # level spacing from the previous close's magnitude: 1.0850 -> 0.01, 50 000 -> 100
        unit = 10.0 ** (np.floor(np.log10(prev.where(prev > 0))) - p["digits"])
        above = np.ceil(prev / unit) * unit
        below = np.floor(prev / unit) * unit
        m = int(p["memory"])
        hi_before = h.shift(1).rolling(m, min_periods=m).max()
        lo_before = lo.shift(1).rolling(m, min_periods=m).min()
        short = (h >= above) & (c < above) & (hi_before < above)
        long_ = (lo <= below) & (c > below) & (lo_before > below)
        sig = pd.Series(np.where(long_, 1.0, np.where(short & p["allow_short"], -1.0, 0.0)),
                        index=bars.index)
        stop = pd.Series(np.where(sig > 0, below - p["stop_frac"] * unit,
                                  above + p["stop_frac"] * unit), index=bars.index)
        target = pd.Series(np.where(sig > 0, c + p["target_frac"] * unit,
                                    c - p["target_frac"] * unit), index=bars.index)
        return _trade(bars, sig, stop, target, max_bars=int(p["max_bars"]))
