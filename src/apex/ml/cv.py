"""Leakage-safe evaluation: purged, embargoed splits and walk-forward predictions."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pandas as pd

from apex.ml.model import GradientBoostedTrees, LogisticModel


def purged_kfold(ends: np.ndarray, n_splits: int = 5,
                 embargo: int = 0) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """K contiguous test folds. A training sample i is dropped if its label window
    [i, ends[i]] overlaps the test fold, or if it starts within ``embargo`` bars after it."""
    n = len(ends)
    idx = np.arange(n)
    for test in np.array_split(idx, n_splits):
        t0, t1 = test[0], test[-1]
        overlap = (idx <= t1) & (ends >= t0)
        embargoed = (idx > t1) & (idx <= t1 + embargo)
        train = idx[~overlap & ~embargoed]
        yield train, test


def walk_forward_proba(x: pd.DataFrame, labels: pd.DataFrame, *, min_train: int = 500,
                       retrain: int = 100, l2: float = 1.0, model: str = "logistic",
                       model_params: dict | None = None) -> pd.Series:
    """Out-of-sample P(label = +1) for every bar, retraining every ``retrain`` bars.

    A model used from bar t on is trained only on samples whose label ended BEFORE t
    (``end < t``), so no training label peeks at bars the model then predicts on.
    """
    feats = x.to_numpy(float)
    y = labels["label"].to_numpy()
    ends = labels["end"].to_numpy()
    ok_x = ~np.isnan(feats).any(axis=1)
    out = np.full(len(x), np.nan)
    for t in range(min_train, len(x), retrain):
        train = ok_x & ~np.isnan(y) & (y != 0) & (ends >= 0) & (ends < t)
        if train.sum() < min_train // 2 or len(np.unique(y[train])) < 2:
            continue
        m = _make_model(model, l2, model_params).fit(feats[train], (y[train] > 0).astype(float))
        seg = slice(t, min(t + retrain, len(x)))
        rows = ok_x[seg]
        p = np.full(rows.shape, np.nan)
        p[rows] = m.predict_proba(feats[seg][rows])
        out[seg] = p
    return pd.Series(out, index=x.index)


def _make_model(kind: str, l2: float, extra: dict | None):
    if kind == "logistic":
        return LogisticModel(l2=l2, **(extra or {}))
    if kind == "gbt":
        return GradientBoostedTrees(l2=l2, **(extra or {}))
    raise ValueError(f"unknown model {kind!r}; use 'logistic' or 'gbt'")


def classification_report(proba: pd.Series, labels: pd.DataFrame) -> dict[str, float]:
    df = pd.DataFrame({"p": proba, "y": labels["label"]}).dropna()
    df = df[df["y"] != 0]
    if df.empty:
        return {"n": 0.0, "accuracy": float("nan"), "base_rate": float("nan"),
                "log_loss": float("nan")}
    yb = (df["y"] > 0).astype(float)
    p = df["p"].clip(1e-9, 1 - 1e-9)
    return {"n": float(len(df)), "accuracy": float(((p > 0.5) == (yb > 0)).mean()),
            "base_rate": float(max(yb.mean(), 1 - yb.mean())),
            "log_loss": float(-(yb * np.log(p) + (1 - yb) * np.log(1 - p)).mean())}
