"""Meta-labelling (hypothesis H50, Lopez de Prado).

A primary strategy decides the *side*; a secondary model decides whether to take each new
primary trade. At every bar where the primary opens a position (an "event"), the meta-model
estimates P(the trade's side hits its favourable triple-barrier first). The trade is taken
only if that probability is at least ``meta_threshold``; a skipped trade stays flat until
the primary's next new position.

Causality: features are causal; the model used at bar t is trained only on earlier events
whose label had finished (``end < t``); it is refitted at most every ``meta_retrain`` bars.
Until ``meta_min_events`` labelled events exist, the primary is followed unchanged
(``meta_warmup="follow"``) or skipped (``"skip"``).
"""

from __future__ import annotations

import dataclasses
import inspect

import numpy as np
import pandas as pd

from apex.ml.features import make_features
from apex.ml.labels import triple_barrier
from apex.ml.model import LogisticModel
from apex.strategies.base import Strategy

META_PARAMS = {"meta_horizon": 10, "meta_min_events": 40, "meta_retrain": 50,
               "meta_l2": 1.0, "meta_threshold": 0.5, "meta_warmup": "follow"}


def events(pos: pd.Series) -> np.ndarray:
    """Positions where the primary opens a new non-zero side."""
    side = np.sign(pos.to_numpy(float))
    prev = np.concatenate([[0.0], side[:-1]])
    return np.flatnonzero((side != 0) & (side != prev))


class MetaLabeled(Strategy):
    """``<KEY>+META``: the inner strategy's trades, filtered by a walk-forward meta-model."""

    def __init__(self, inner: Strategy, key: str | None = None) -> None:
        self.inner = inner
        self.meta = self.make_meta(inner.meta, key)
        self.last_report: dict = {}

    @staticmethod
    def make_meta(m, key: str | None = None):  # noqa: ANN001, ANN205
        return dataclasses.replace(
            m, key=key or f"{m.key}+META", name=f"{m.name} (meta-labelled)",
            category="machine_learning" if key else m.category,
            description=m.description + " Each new trade is taken only if a walk-forward "
            "logistic meta-model gives it at least meta_threshold probability of reaching "
            "its profit barrier first.",
            default_params={**m.default_params, **META_PARAMS})

    def source_code(self) -> str:
        return (inspect.getsource(MetaLabeled) + inspect.getsource(type(self))
                + self.inner.source_code())

    def target_position(self, bars: pd.DataFrame, params: dict) -> pd.Series:
        inner_p = {k: v for k, v in params.items() if k not in META_PARAMS}
        pos = self.inner.target_position(bars, inner_p).reindex(bars.index).fillna(0.0)
        side = np.sign(pos.to_numpy(float))
        ev = events(pos)
        lab = triple_barrier(bars, horizon=int(params["meta_horizon"]))
        feats = make_features(bars).to_numpy(float)
        x = np.column_stack([feats, side])
        y = lab["label"].to_numpy(float) * side  # >0: the trade's side won
        ends = lab["end"].to_numpy(float)
        ok = ~np.isnan(x).any(axis=1) & ~np.isnan(y) & (y != 0)

        take = np.ones(len(bars), dtype=bool)
        proba = np.full(len(bars), np.nan)
        model, fitted_at = None, -10**9
        for k, t in enumerate(ev):
            if t - fitted_at >= params["meta_retrain"]:
                past = ev[:k]
                train = past[ok[past] & (ends[past] < t)]
                if len(train) >= params["meta_min_events"] and \
                        len(np.unique(y[train] > 0)) == 2:
                    model = LogisticModel(l2=params["meta_l2"]).fit(
                        x[train], (y[train] > 0).astype(float))
                    fitted_at = t
            if model is not None and not np.isnan(x[t]).any():
                proba[t] = float(model.predict_proba(x[t:t + 1])[0])
                take_t = proba[t] >= params["meta_threshold"]
            else:
                take_t = params["meta_warmup"] == "follow"
            end = ev[k + 1] if k + 1 < len(ev) else len(bars)
            take[t:end] = take_t
        out = np.where(take, pos.to_numpy(float), 0.0)
        scored = ~np.isnan(proba[ev]) if len(ev) else np.array([], bool)
        self.last_report = {"events": int(len(ev)), "scored": int(scored.sum()),
                            "taken": int(sum(take[t] for t in ev[scored])) if len(ev) else 0}
        return pd.Series(out, index=bars.index)
