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
    legs: int = 2  # number of instruments, in the order the caller passes them

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
        n, lb = len(yb), int(params["lookback"])
        ly, lx = np.log(yb["close"].to_numpy(float)), np.log(xb["close"].to_numpy(float))
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
        return pd.DataFrame({ys: wy, xs: wx}, index=yb.index)
