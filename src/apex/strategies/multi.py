"""Strategies that trade several instruments together (spec H38-H41, H05).

A multi-instrument strategy sees the bars of all its legs, aligned on common timestamps,
and returns a target *weight* per leg: signed notional as a fraction of equity. The same
causality contract as single-instrument strategies applies: the weights at bar t may only
use bars with ts <= t, and the portfolio backtester fills them at the next bar's open.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

import numpy as np
import pandas as pd

from apex.domain.market import AssetClass, Timeframe
from apex.strategies.base import Strategy, StrategyMeta

_MULTI: dict[str, type[MultiStrategy]] = {}


class MultiStrategy(Strategy):
    legs: int | None = 2  # instruments, in the order the caller passes them; None = 2 or more

    @classmethod
    def check_legs(cls, n: int) -> None:
        ok = n >= 2 if cls.legs is None else n == cls.legs
        if not ok:
            want = "2 or more" if cls.legs is None else str(cls.legs)
            raise ValueError(f"{cls.meta.key} needs {want} instruments, got {n}")

    def target_position(self, bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
        raise TypeError(f"{self.meta.key} trades several instruments; use target_weights")

    @abstractmethod
    def target_weights(self, bars: dict[str, pd.DataFrame], params: dict[str, Any]
                       ) -> pd.DataFrame:
        """``bars``: one frame per leg (insertion order = leg order), all with the same
        ``ts``. Returns a frame indexed like them with one weight column per symbol."""


def register_multi(cls: type[MultiStrategy]) -> type[MultiStrategy]:
    if cls.meta.key in _MULTI and _MULTI[cls.meta.key] is not cls:
        raise ValueError(f"duplicate strategy key {cls.meta.key}")
    _MULTI[cls.meta.key] = cls
    return cls


def all_multi_strategies() -> dict[str, type[MultiStrategy]]:
    return dict(_MULTI)


def align(bars: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Keep only timestamps every leg traded at, so each row is one moment in time."""
    common = None
    for df in bars.values():
        ts = pd.Index(df["ts"])
        common = ts if common is None else common.intersection(ts)
    out = {}
    for sym, df in bars.items():
        out[sym] = df[df["ts"].isin(common)].sort_values("ts").reset_index(drop=True)
    return out


def spread_reversion_weights(ly: np.ndarray, lx: np.ndarray, params: dict[str, Any]
                             ) -> tuple[np.ndarray, np.ndarray]:
    """Z-score reversion on the spread ly - beta*lx (beta from a rolling regression).

    Returns weights for y and x. Causal: bar t uses log prices up to t only. Params:
    lookback, entry_z, exit_z, stop_z, max_bars, gross.
    """
    n, lb = len(ly), int(params["lookback"])
    sy, sx = pd.Series(ly), pd.Series(lx)
    beta = (sy.rolling(lb).cov(sx) / sx.rolling(lb).var()).to_numpy()
    spread = ly - beta * lx
    # z of today's spread against the window's spreads, all with today's beta
    z = np.full(n, np.nan)
    for t in range(lb - 1, n):
        if not np.isfinite(beta[t]):
            continue
        w = ly[t - lb + 1:t + 1] - beta[t] * lx[t - lb + 1:t + 1]
        sd = w.std()
        z[t] = (spread[t] - w.mean()) / sd if sd > 0 else np.nan
    wy, wx = np.zeros(n), np.zeros(n)
    side, held, cool, b0 = 0, 0, False, 0.0
    ez, xz, sz, mb, g = (params["entry_z"], params["exit_z"], params["stop_z"],
                         int(params["max_bars"]), params["gross"])
    for t in range(n):
        zt = z[t]
        if not np.isfinite(zt):
            side = 0
        elif side:
            held += 1
            stop = abs(zt) >= sz
            done = (side > 0 and zt >= -xz) or (side < 0 and zt <= xz)
            if stop or done or (mb and held >= mb):
                side, cool = 0, stop
        elif cool:
            cool = abs(zt) >= ez
        elif zt <= -ez and abs(zt) < sz:
            side, held, b0 = 1, 0, beta[t]  # spread cheap: buy y, sell x
        elif zt >= ez and abs(zt) < sz:
            side, held, b0 = -1, 0, beta[t]
        if side:
            k = g / (1 + abs(b0))
            wy[t], wx[t] = side * k, -side * k * b0
    return wy, wx


@register_multi
class PairsZScore(MultiStrategy):
    """Rolling-hedge-ratio spread reversion between two related instruments."""

    meta = StrategyMeta(
        key="STAT-PAIR-001", version="1.0.0", name="Pairs spread z-score reversion",
        category="statistical_arbitrage",
        description="Trades the log-price spread y - beta*x of two related instruments "
                    "(beta from a rolling regression) when it is stretched, expecting it to "
                    "revert. Hypotheses H38 (equity pairs) and H40 (BTC/ETH).",
        entry_rules="spread z-score beyond +/- entry_z: short the rich leg, buy the cheap one",
        exit_rules="z back through exit_z, or max_bars held",
        stop_loss="z beyond +/- stop_z (spread kept widening); no re-entry until z is back "
                  "inside entry_z",
        take_profit="z crosses exit_z",
        position_sizing="gross weight split 1 : |beta| between the legs; hedge ratio frozen "
                        "at entry so the book is not re-traded every bar",
        asset_classes=(AssetClass.EQUITY, AssetClass.CRYPTO, AssetClass.FUTURES,
                       AssetClass.FOREX),
        timeframes=(Timeframe.H1, Timeframe.D1),
        default_params={"lookback": 60, "entry_z": 2.0, "exit_z": 0.0, "stop_z": 4.0,
                        "max_bars": 30, "gross": 1.0},
    )

    def target_weights(self, bars: dict[str, pd.DataFrame], params: dict[str, Any]
                       ) -> pd.DataFrame:
        (ys, yb), (xs, xb) = list(bars.items())[:2]
        wy, wx = spread_reversion_weights(np.log(yb["close"].to_numpy(float)),
                                          np.log(xb["close"].to_numpy(float)), params)
        return pd.DataFrame({ys: wy, xs: wx}, index=yb.index)


@register_multi
class CrossAssetTrend(MultiStrategy):
    """Diversified time-series trend across several markets, inverse-volatility weighted."""

    legs = None
    meta = StrategyMeta(
        key="TF-XASSET-001", version="1.0.0", name="Cross-asset trend portfolio",
        category="trend_following",
        description="Holds each market long or short by the sign of its trailing return and "
                    "sizes legs by inverse volatility so quiet and wild markets carry similar "
                    "risk. Rebalanced on a fixed schedule. Hypothesis H05.",
        entry_rules="at each rebalance: long if lookback-bar return > 0, short if < 0 "
                    "(flat if long_only and the return is negative)",
        exit_rules="sign of the trailing return flips at a rebalance",
        stop_loss="none (diversification and volatility scaling instead)",
        take_profit="none",
        position_sizing="weights proportional to 1 / realised volatility, scaled to gross; "
                        "held unchanged between rebalances",
        asset_classes=(AssetClass.FUTURES, AssetClass.FOREX, AssetClass.EQUITY,
                       AssetClass.CRYPTO),
        timeframes=(Timeframe.D1,),
        default_params={"lookback": 120, "vol_window": 60, "rebalance": 21, "gross": 1.0,
                        "long_only": False},
    )

    def target_weights(self, bars: dict[str, pd.DataFrame], params: dict[str, Any]
                       ) -> pd.DataFrame:
        syms = list(bars)
        close = pd.DataFrame({s: bars[s]["close"].to_numpy(float) for s in syms})
        lb, vw, rb = int(params["lookback"]), int(params["vol_window"]), int(params["rebalance"])
        mom = (close / close.shift(lb) - 1).to_numpy()
        vol = np.log(close).diff().rolling(vw).std().to_numpy()
        n = len(close)
        w = np.zeros((n, len(syms)))
        cur = np.zeros(len(syms))
        for t in range(n):
            if t % max(rb, 1) == 0:  # fixed calendar of rebalances, independent of later bars
                sig = np.sign(np.nan_to_num(mom[t]))
                if params["long_only"]:
                    sig = np.clip(sig, 0, None)
                inv = np.where(np.isfinite(vol[t]) & (vol[t] > 0), 1 / vol[t], 0.0) * (sig != 0)
                tot = inv.sum()
                cur = sig * inv / tot * params["gross"] if tot > 0 else np.zeros(len(syms))
            w[t] = cur
        return pd.DataFrame(w, columns=syms, index=next(iter(bars.values())).index)


@register_multi
class CrossSectionalMomentum(MultiStrategy):
    """Rank a basket by past return (skipping the latest bars); hold winners, short losers."""

    legs = None
    meta = StrategyMeta(
        key="MOM-XSEC-001", version="1.0.0", name="Cross-sectional momentum",
        category="momentum",
        description="At each rebalance ranks every instrument by its return from lookback "
                    "bars ago to skip bars ago (the classic 12-1 month idea) and buys the "
                    "top fraction, shorting the bottom fraction unless long_only. Equal "
                    "weight within each side. Hypothesis H07.",
        entry_rules="at a rebalance: in the top `top` fraction by ranked return -> long; in "
                    "the bottom fraction -> short (unless long_only)",
        exit_rules="drops out of its group at a later rebalance",
        stop_loss="none at instrument level (portfolio drawdown limits live in the risk layer)",
        take_profit="none",
        position_sizing="gross split equally between the two sides, equal weight within a "
                        "side (all of gross on the long side when long_only)",
        asset_classes=(AssetClass.EQUITY, AssetClass.CRYPTO, AssetClass.FUTURES,
                       AssetClass.FOREX),
        timeframes=(Timeframe.D1,),
        default_params={"lookback": 120, "skip": 5, "rebalance": 21, "top": 0.34,
                        "gross": 1.0, "long_only": False},
    )

    def target_weights(self, bars: dict[str, pd.DataFrame], params: dict[str, Any]
                       ) -> pd.DataFrame:
        syms = list(bars)
        close = pd.DataFrame({s: bars[s]["close"].to_numpy(float) for s in syms})
        lb, sk, rb = int(params["lookback"]), int(params["skip"]), max(int(params["rebalance"]), 1)
        if not 0 <= sk < lb:
            raise ValueError("need 0 <= skip < lookback")
        ret = (close.shift(sk) / close.shift(lb) - 1).to_numpy()
        n, m = ret.shape
        k = max(1, int(round(params["top"] * m)))
        if not params["long_only"]:
            k = min(k, m // 2)  # a name can't be both a winner and a loser
        g, lo = params["gross"], params["long_only"]
        w, cur = np.zeros((n, m)), np.zeros(m)
        for t in range(n):
            if t % rb == 0:
                cur = np.zeros(m)
                r = ret[t]
                if np.isfinite(r).all() and k > 0:
                    order = np.argsort(-r, kind="stable")  # ties broken by leg order
                    if lo:
                        cur[order[:k]] = g / k
                    else:
                        cur[order[:k]] = g / 2 / k
                        cur[order[-k:]] = -g / 2 / k
            w[t] = cur
        return pd.DataFrame(w, columns=syms, index=next(iter(bars.values())).index)


@register_multi
class EtfVsBasket(MultiStrategy):
    """First leg is an ETF or index future, the rest its main holdings: trade the gap."""

    legs = None
    meta = StrategyMeta(
        key="STAT-BASKET-001", version="1.0.0", name="ETF vs basket reversion",
        category="statistical_arbitrage",
        description="Builds an equal-weight basket from legs 2..n (log-price average), "
                    "regresses the first leg on it over a rolling window and fades stretched "
                    "gaps, expecting the ETF and its holdings to re-converge. Hypothesis H41.",
        entry_rules="gap z-score beyond +/- entry_z: short the rich side, buy the cheap side",
        exit_rules="z back through exit_z, or max_bars held (about a day of 5-minute bars)",
        stop_loss="z beyond +/- stop_z; no re-entry until z is back inside entry_z",
        take_profit="z crosses exit_z",
        position_sizing="gross split 1 : |beta| between the ETF and the basket, the basket "
                        "side split equally across its members; hedge ratio frozen at entry",
        asset_classes=(AssetClass.EQUITY, AssetClass.FUTURES),
        timeframes=(Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.D1),
        default_params={"lookback": 120, "entry_z": 2.5, "exit_z": 0.0, "stop_z": 5.0,
                        "max_bars": 78, "gross": 1.0},
    )

    @classmethod
    def check_legs(cls, n: int) -> None:
        if n < 3:
            raise ValueError(f"{cls.meta.key} needs the ETF plus at least 2 holdings, got {n}")

    def target_weights(self, bars: dict[str, pd.DataFrame], params: dict[str, Any]
                       ) -> pd.DataFrame:
        syms = list(bars)
        logs = {s: np.log(bars[s]["close"].to_numpy(float)) for s in syms}
        basket = np.mean([logs[s] for s in syms[1:]], axis=0)
        wy, wx = spread_reversion_weights(logs[syms[0]], basket, params)
        out = {syms[0]: wy}
        for s in syms[1:]:
            out[s] = wx / (len(syms) - 1)
        return pd.DataFrame(out, index=bars[syms[0]].index)
