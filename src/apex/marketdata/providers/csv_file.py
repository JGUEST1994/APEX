"""Load bars from local CSV files (vendor exports, purchased datasets, Kaggle-style data).

Expected file layout: ``<root>/<SYMBOL>_<timeframe>.csv`` with a header containing a
timestamp column (ts/timestamp/time/date/datetime) and open/high/low/close[/volume].
Naive timestamps are interpreted as UTC unless ``assume_tz`` is given.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from apex.core.errors import ProviderError
from apex.domain.market import InstrumentSpec, Timeframe
from apex.marketdata.base import MarketDataProvider


class CsvFileProvider(MarketDataProvider):
    name = "csv"

    def __init__(self, root: str | Path, assume_tz: str = "UTC") -> None:
        self.root = Path(root)
        self.assume_tz = assume_tz

    def path_for(self, spec: InstrumentSpec, timeframe: Timeframe) -> Path:
        return self.root / f"{spec.symbol}_{timeframe.value}.csv"

    def supports(self, spec: InstrumentSpec, timeframe: Timeframe) -> bool:
        return self.path_for(spec, timeframe).is_file()

    def _fetch(self, spec, timeframe, start, end) -> pd.DataFrame:
        path = self.path_for(spec, timeframe)
        try:
            df = pd.read_csv(path)
        except Exception as exc:  # pragma: no cover - passthrough
            raise ProviderError(f"cannot read {path}: {exc}") from exc
        df.columns = [str(c).strip().lower() for c in df.columns]
        tcol = next((c for c in ("ts", "timestamp", "time", "date", "datetime") if c in df), None)
        if tcol is None:
            raise ProviderError(f"{path} has no timestamp column")
        ts = pd.to_datetime(df[tcol])
        if ts.dt.tz is None:
            ts = ts.dt.tz_localize(self.assume_tz)
        df[tcol] = ts.dt.tz_convert("UTC")
        return df
