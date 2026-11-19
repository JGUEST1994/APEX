"""Cross-check the same instrument from two sources (spec Section 10: data validation)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class ReconcileReport:
    compared: int
    only_in_a: list[pd.Timestamp] = field(default_factory=list)
    only_in_b: list[pd.Timestamp] = field(default_factory=list)
    mismatches: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def ok(self) -> bool:
        return not self.only_in_a and not self.only_in_b and self.mismatches.empty


def reconcile(a: pd.DataFrame, b: pd.DataFrame, tolerance_bps: float = 5.0) -> ReconcileReport:
    """Compare closes on common timestamps; flag differences above ``tolerance_bps``."""
    ia, ib = a.set_index("ts"), b.set_index("ts")
    common = ia.index.intersection(ib.index)
    diff_bps = ((ia.loc[common, "close"] - ib.loc[common, "close"]).abs()
                / ib.loc[common, "close"] * 1e4)
    bad = diff_bps[diff_bps > tolerance_bps]
    mism = pd.DataFrame({"ts": bad.index, "close_a": ia.loc[bad.index, "close"].to_numpy(),
                         "close_b": ib.loc[bad.index, "close"].to_numpy(),
                         "diff_bps": bad.to_numpy()})
    return ReconcileReport(
        compared=len(common),
        only_in_a=list(ia.index.difference(ib.index)),
        only_in_b=list(ib.index.difference(ia.index)),
        mismatches=mism)
