"""Risk limits. Limits may be TIGHTENED automatically but never loosened automatically.

A person may set looser limits for one paper account (``apex paper-create --limit``): that is
the "human config change", and it needs a named person, who is recorded in the audit log.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields

NOT_A_PERSON = ("", "system", "risk", "scheduler", "apex")


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

    def changed(self, changes: dict[str, object]) -> RiskLimits:
        """New limits with ``changes`` set by a person (``paper-create --limit``). Unlike
        :meth:`tightened` this may loosen a limit, so the caller must check
        :meth:`looser_than` and require a named person. Each value must be a positive, finite
        number, and a whole number for the counts (positions, losing trades)."""
        current = asdict(self)
        new = {}
        for k, v in changes.items():
            if k not in current:
                raise ValueError(f"unknown safety limit {k!r} (known: {', '.join(current)})")
            if (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                    or v <= 0):
                raise ValueError(f"{k} must be a positive number, not {v!r}")
            if isinstance(current[k], int):
                if v != int(v):
                    raise ValueError(f"{k} must be a whole number, not {v!r}")
                new[k] = int(v)
            else:
                new[k] = float(v)
        return RiskLimits(**{**current, **new})

    def looser_than(self, base: RiskLimits | None = None) -> dict[str, tuple[float, float]]:
        """The limits set looser than ``base`` (default: the standard limits), as
        ``{name: (base value, this value)}``. Every limit is a maximum, so higher is looser."""
        ref = asdict(base or RiskLimits())
        return {k: (ref[k], v) for k, v in asdict(self).items() if v > ref[k]}

    def differences(self, base: RiskLimits | None = None) -> dict[str, tuple[float, float]]:
        """Every limit that differs from ``base`` (default: the standard limits), tighter or
        looser, as ``{name: (base value, this value)}``."""
        ref = asdict(base or RiskLimits())
        return {k: (ref[k], v) for k, v in asdict(self).items() if v != ref[k]}

    @classmethod
    def names(cls) -> list[str]:
        return [f.name for f in fields(cls)]
