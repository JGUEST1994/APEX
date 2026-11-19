"""Reference strategy used to exercise the interface. It is a hypothesis, not a
recommendation; it has not been validated."""

from __future__ import annotations

import pandas as pd

from apex.domain.market import AssetClass, Timeframe
from apex.strategies.base import Strategy, StrategyMeta
from apex.strategies.registry import register


@register
class SmaCrossover(Strategy):
    meta = StrategyMeta(
        key="TF-SMA-001", version="0.1.0", name="SMA crossover", category="trend_following",
        description="Long when fast SMA of close is above slow SMA, short when below.",
        entry_rules="sign(SMA(close, fast) - SMA(close, slow)) changes",
        exit_rules="opposite crossover",
        stop_loss="none in v0.1 (to be added with risk engine)",
        take_profit="none",
        position_sizing="fixed fraction, delegated to risk engine",
        asset_classes=tuple(AssetClass), timeframes=(Timeframe.H1, Timeframe.D1),
        regimes=("trending",), default_params={"fast": 20, "slow": 50, "allow_short": True},
    )

    def target_position(self, bars: pd.DataFrame, params: dict) -> pd.Series:
        if params["fast"] >= params["slow"]:
            raise ValueError("fast must be < slow")
        fast = bars["close"].rolling(params["fast"], min_periods=params["fast"]).mean()
        slow = bars["close"].rolling(params["slow"], min_periods=params["slow"]).mean()
        pos = (fast > slow).astype(float) - (fast < slow).astype(float)
        if not params["allow_short"]:
            pos = pos.clip(lower=0)
        return pos.where(slow.notna(), 0.0)
