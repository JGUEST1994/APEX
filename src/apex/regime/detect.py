"""Regime labels used as strategy filters.

Labels match ``StrategyMeta.regimes``: ``trending`` / ``range`` for direction, and a
separate volatility state ``low_vol`` / ``normal_vol`` / ``high_vol``. Every function is
causal: the label at bar t uses bars <= t only (thresholds come from trailing windows or
from a model fitted on an earlier training window).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex.regime.hmm import GaussianHMM


def efficiency_ratio(close: pd.Series, n: int = 20) -> pd.Series:
    """Kaufman efficiency ratio: |net move| / path length over n bars, in [0, 1]."""
    net = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n, min_periods=n).sum()
    return (net / path.replace(0, np.nan)).clip(0, 1)


def trend_regime(bars: pd.DataFrame, n: int = 20, threshold: float = 0.3) -> pd.Series:
    er = efficiency_ratio(bars["close"], n)
    lab = pd.Series(np.where(er >= threshold, "trending", "range"), index=bars.index,
                    dtype=object)
    return lab.where(er.notna(), None)


def volatility_regime(bars: pd.DataFrame, n: int = 20, lookback: int = 250,
                      low: float = 0.25, high: float = 0.75) -> pd.Series:
    """Realised-vol percentile within a trailing window (current bar included)."""
    rv = np.log(bars["close"]).diff().rolling(n, min_periods=n).std()
    pct = rv.rolling(lookback, min_periods=lookback // 2).rank(pct=True)
    lab = pd.Series(np.select([pct <= low, pct >= high], ["low_vol", "high_vol"],
                              "normal_vol"), index=bars.index, dtype=object)
    return lab.where(pct.notna(), None)


def hmm_regime(bars: pd.DataFrame, train_end: int, n_states: int = 2,
               seed: int = 0) -> tuple[pd.Series, GaussianHMM]:
    """Fit an HMM on log returns of bars[:train_end], then FILTER the whole series.

    Returns the most likely filtered state per bar (0 = calmest) and the fitted model.
    """
    r = np.log(bars["close"]).diff().to_numpy()
    model = GaussianHMM(n_states=n_states, seed=seed).fit(r[1:train_end])
    probs = model.filter(r)
    state = pd.Series(probs.argmax(axis=1), index=bars.index)
    state.iloc[0] = -1  # no return on the first bar
    return state, model


def stability(labels: pd.Series) -> dict[str, float]:
    """Switch rate and mean run length of a label series (ignoring missing labels)."""
    s = labels.dropna()
    s = s[s != -1] if s.dtype != object else s
    if len(s) < 2:
        return {"switch_rate": float("nan"), "mean_run": float("nan"), "n": float(len(s))}
    switches = int((s != s.shift()).iloc[1:].sum())
    return {"switch_rate": switches / (len(s) - 1), "mean_run": len(s) / (switches + 1),
            "n": float(len(s))}


def forward_vol_by_state(bars: pd.DataFrame, state: pd.Series, start: int,
                         horizon: int = 20) -> dict:
    """Out-of-sample check: mean realised vol over the NEXT ``horizon`` bars per state,
    using only bars from ``start`` on. A useful volatility regime has calm states followed
    by calmer markets than turbulent states."""
    r = np.log(bars["close"]).diff()
    fwd = r[::-1].rolling(horizon, min_periods=horizon).std()[::-1].shift(-1)
    df = pd.DataFrame({"state": state, "fwd": fwd}).iloc[start:].dropna()
    df = df[df["state"] != -1]
    return {str(k): float(v) for k, v in df.groupby("state")["fwd"].mean().items()}


def apply_regime_filter(position: pd.Series, labels: pd.Series,
                        allowed: tuple[str, ...]) -> pd.Series:
    """Zero the position wherever the regime is not in ``allowed`` ("any" allows all)."""
    if "any" in allowed:
        return position
    ok = labels.isin(allowed)
    return position.where(ok, 0.0)
