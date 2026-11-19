"""Unified market data interface (Section 10).

Every provider returns bars in one canonical shape so the rest of APEX never sees vendor
formats. Canonical frame: columns ``ts`` (UTC-aware bar open time), ``open``, ``high``,
``low``, ``close``, ``volume``; sorted ascending by ``ts``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from apex.core.errors import ProviderError
from apex.domain.market import InstrumentSpec, Timeframe

BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


class MarketDataProvider(ABC):
    """Interchangeable source of historical bars."""

    name: str = "abstract"

    @abstractmethod
    def supports(self, spec: InstrumentSpec, timeframe: Timeframe) -> bool: ...

    @abstractmethod
    def _fetch(
        self, spec: InstrumentSpec, timeframe: Timeframe, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Return bars in [start, end) in any reasonable shape; ``fetch_bars`` normalises."""

    def fetch_bars(
        self, spec: InstrumentSpec, timeframe: Timeframe, start: datetime, end: datetime
    ) -> pd.DataFrame:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware")
        if start >= end:
            raise ValueError("start must be before end")
        if not self.supports(spec, timeframe):
            raise ProviderError(f"{self.name} does not support {spec.symbol} {timeframe.value}")
        df = normalise_bars(self._fetch(spec, timeframe, start, end))
        return df[(df["ts"] >= start) & (df["ts"] < end)].reset_index(drop=True)


def normalise_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a vendor frame into the canonical bar frame. Does not drop bad rows."""
    if df.empty:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in BAR_COLUMNS}).assign(
            ts=pd.Series(dtype="datetime64[ns, UTC]")
        )[BAR_COLUMNS]
    renamed = df.rename(columns=lambda c: str(c).strip().lower())
    aliases = {"timestamp": "ts", "time": "ts", "date": "ts", "datetime": "ts",
               "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "vol": "volume"}
    renamed = renamed.rename(columns={k: v for k, v in aliases.items() if k in renamed.columns})
    missing = {"ts", "open", "high", "low", "close"} - set(renamed.columns)
    if missing:
        raise ProviderError(f"bar data missing columns: {sorted(missing)}")
    if "volume" not in renamed.columns:
        renamed["volume"] = 0.0
    out = renamed[BAR_COLUMNS].copy()
    ts = pd.to_datetime(out["ts"], utc=False)
    out["ts"] = ts.dt.tz_localize("UTC") if ts.dt.tz is None else ts.dt.tz_convert("UTC")
    for c in BAR_COLUMNS[1:]:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")
    return out.sort_values("ts", kind="stable").reset_index(drop=True)
