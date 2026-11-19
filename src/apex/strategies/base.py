"""Common strategy interface and lifecycle (Sections 6 and 8).

A strategy is a pure function of past bars -> target position. It never places orders;
signals flow to risk management and then the execution engine (neither exists yet).

Contract that every implementation must honour (tested in tests/test_strategies.py):
the signal at bar t may only use bars with ts <= t (no look-ahead). The backtester will act
on a signal no earlier than the next bar's open.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

from apex.domain.market import AssetClass, Timeframe


class StrategyStatus(str, Enum):
    EXPERIMENTAL = "experimental"
    BACKTESTING = "backtesting"
    VALIDATION = "validation"
    PAPER_TRADING = "paper_trading"
    APPROVED = "approved"
    SUSPENDED = "suspended"
    REJECTED = "rejected"


# Strategies only move forward through the validation pipeline one step at a time.
# Any active state can be suspended or rejected; nothing jumps straight to APPROVED.
_ALLOWED: dict[StrategyStatus, set[StrategyStatus]] = {
    StrategyStatus.EXPERIMENTAL: {StrategyStatus.BACKTESTING},
    StrategyStatus.BACKTESTING: {StrategyStatus.VALIDATION, StrategyStatus.EXPERIMENTAL},
    StrategyStatus.VALIDATION: {StrategyStatus.PAPER_TRADING, StrategyStatus.BACKTESTING},
    StrategyStatus.PAPER_TRADING: {StrategyStatus.APPROVED},
    StrategyStatus.APPROVED: set(),
    StrategyStatus.SUSPENDED: {StrategyStatus.VALIDATION},  # must re-validate to come back
    StrategyStatus.REJECTED: set(),
}
for _s in list(_ALLOWED):
    if _s not in (StrategyStatus.REJECTED,):
        _ALLOWED[_s] |= {StrategyStatus.REJECTED}
    if _s not in (StrategyStatus.REJECTED, StrategyStatus.SUSPENDED):
        _ALLOWED[_s] |= {StrategyStatus.SUSPENDED}


def can_transition(src: StrategyStatus, dst: StrategyStatus) -> bool:
    return dst in _ALLOWED[src]


@dataclass(frozen=True)
class StrategyMeta:
    key: str  # unique id, e.g. "TF-SMA-001"
    version: str  # semantic version of the rules
    name: str
    category: str  # momentum, mean_reversion, breakout, vwap, ...
    description: str
    entry_rules: str
    exit_rules: str
    stop_loss: str
    take_profit: str
    position_sizing: str
    asset_classes: tuple[AssetClass, ...]
    timeframes: tuple[Timeframe, ...]
    regimes: tuple[str, ...] = ("any",)
    cost_assumptions: str = "instrument CostModel"
    default_params: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    meta: StrategyMeta

    def params(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        unknown = set(overrides or {}) - set(self.meta.default_params)
        if unknown:
            raise ValueError(f"unknown parameters for {self.meta.key}: {sorted(unknown)}")
        return {**self.meta.default_params, **(overrides or {})}

    def source_code(self) -> str:
        """Source used to fingerprint a strategy version (wrappers include the inner one)."""
        import inspect

        return inspect.getsource(type(self))

    @abstractmethod
    def target_position(self, bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
        """Return a Series aligned to ``bars`` with values in [-1, 1] (fraction of max size)."""
