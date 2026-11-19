"""ML-driven strategy (hypothesis H53): walk-forward logistic model on causal
features predicting triple-barrier outcomes. Registered as an experimental strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex.ml.cv import walk_forward_proba
from apex.ml.features import make_features
from apex.ml.labels import triple_barrier
from apex.ml.meta import MetaLabeled
from apex.strategies.base import Strategy
from apex.strategies.builtin import SWING, DonchianBreakout, _meta
from apex.strategies.registry import register


@register
class MlLogisticTripleBarrier(Strategy):
    meta = _meta("ML-LOGIT-001", "Walk-forward logistic model", "machine_learning",
                 "Logistic regression on causal price/volatility/volume features predicting "
                 "triple-barrier outcomes; retrained walk-forward with purged labels.",
                 "P(up) > `enter` (long) / < 1 - `enter` (short)",
                 "probability falls back inside the band", "triple-barrier stop (implicit)",
                 "triple-barrier target (implicit)",
                 {"horizon": 10, "min_train": 500, "retrain": 100, "l2": 1.0, "enter": 0.55,
                  "allow_short": True}, tfs=SWING)

    def target_position(self, bars, p):
        x = make_features(bars)
        labels = triple_barrier(bars, horizon=p["horizon"])
        proba = walk_forward_proba(x, labels, min_train=p["min_train"],
                                   retrain=p["retrain"], l2=p["l2"])
        long_ = proba > p["enter"]
        short = (proba < 1 - p["enter"]) & p["allow_short"]
        return pd.Series(np.where(long_, 1.0, np.where(short, -1.0, 0.0)), index=bars.index)


@register
class MetaLabeledDonchian(MetaLabeled):
    """H50 reference: Donchian breakout (primary side) + walk-forward meta-model."""

    meta = MetaLabeled.make_meta(DonchianBreakout.meta, "ML-META-001")

    def __init__(self) -> None:
        super().__init__(DonchianBreakout(), key="ML-META-001")
