"""Wrap any strategy so it only trades in the regimes its metadata declares."""

from __future__ import annotations

import dataclasses
import inspect

import pandas as pd

from apex.regime.detect import apply_regime_filter, trend_regime
from apex.strategies.base import Strategy


class RegimeFiltered(Strategy):
    """``<KEY>+RF``: the inner strategy's position, zeroed outside ``meta.regimes``.

    Uses the causal efficiency-ratio trend regime (``trending`` / ``range``). Strategies
    declaring ``("any",)`` are unchanged.
    """

    def __init__(self, inner: Strategy) -> None:
        self.inner = inner
        m = inner.meta
        self.meta = dataclasses.replace(
            m, key=f"{m.key}+RF", name=f"{m.name} (regime filtered)",
            description=m.description + " Trades only in regimes " + ", ".join(m.regimes)
            + " (efficiency-ratio regime filter).",
            default_params={**m.default_params, "rf_n": 20, "rf_threshold": 0.3})

    def source_code(self) -> str:
        return inspect.getsource(type(self)) + self.inner.source_code()

    def target_position(self, bars: pd.DataFrame, params: dict) -> pd.Series:
        inner_p = {k: v for k, v in params.items() if k not in ("rf_n", "rf_threshold")}
        pos = self.inner.target_position(bars, inner_p)
        labels = trend_regime(bars, params["rf_n"], params["rf_threshold"])
        return apply_regime_filter(pos, labels, self.inner.meta.regimes)
