"""Risk limits. Limits may be TIGHTENED automatically but never loosened automatically."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class RiskLimits:
    max_risk_per_trade: float = 0.005  # fraction of equity lost if the stop is hit
    max_position_notional: float = 0.20  # fraction of equity per instrument
    max_gross_exposure: float = 1.0  # sum |notional| / equity
    max_net_exposure: float = 1.0  # |sum notional| / equity
    max_leverage: float = 2.0
    max_positions: int = 10
    max_daily_loss: float = 0.02  # fraction of start-of-day equity
    max_drawdown: float = 0.10  # from equity peak; breach trips the kill switch
    max_consecutive_losses: int = 6
    max_cluster_exposure: float = 0.40  # gross notional of one correlated group / equity

    def tightened(self, **changes: float) -> RiskLimits:
        """Return new limits; raises if any change would loosen a limit."""
        current = asdict(self)
        for k, v in changes.items():
            if k not in current:
                raise KeyError(k)
            if v > current[k]:
                raise ValueError(f"{k}: {v} would loosen the limit ({current[k]}); "
                                 "loosening needs a human config change")
            if v <= 0:
                raise ValueError(f"{k} must be positive")
        return RiskLimits(**{**current, **changes})

    @classmethod
    def names(cls) -> list[str]:
        return [f.name for f in fields(cls)]
