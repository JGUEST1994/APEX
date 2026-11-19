"""Causal feature matrix from OHLCV bars. Row t uses bars <= t only."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex.regime.detect import efficiency_ratio
from apex.strategies import indicators as ind


def make_features(bars: pd.DataFrame, lags: tuple[int, ...] = (1, 2, 5, 10, 20)) -> pd.DataFrame:
    c = bars["close"]
    lr = np.log(c).diff()
    f = {f"ret_{k}": np.log(c / c.shift(k)) for k in lags}
    vol20 = lr.rolling(20, min_periods=20).std()
    f["vol_20"] = vol20
    f["vol_ratio"] = lr.rolling(5, min_periods=5).std() / vol20
    f["rsi_14"] = (ind.rsi(c, 14) - 50) / 50
    f["er_20"] = efficiency_ratio(c, 20)
    f["dist_sma_50"] = np.log(c / ind.sma(c, 50))
    f["range_atr"] = (bars["high"] - bars["low"]) / ind.atr(bars, 14)
    rng = (bars["high"] - bars["low"]).replace(0, np.nan)
    f["ibs"] = (c - bars["low"]) / rng
    v = bars["volume"].astype(float)
    vm, vs = v.rolling(20, min_periods=20).mean(), v.rolling(20, min_periods=20).std()
    f["volume_z"] = ((v - vm) / vs.replace(0, np.nan)).fillna(0.0)
    return pd.DataFrame(f, index=bars.index)
