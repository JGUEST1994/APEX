"""Continuous futures from one CSV file per contract.

Layout: ``<root>/<SYMBOL>/<contract>_<timeframe>.csv`` (for example
``data/csv/ES/ESH5_1d.csv``, ``ESM5_1d.csv``), same columns as :mod:`csv_file`. Contracts
are ordered by their last bar (the expiry proxy), so file names need no month codes.
Source names ``csv-cont`` (difference adjusted) and ``csv-cont-ratio``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from apex.core.errors import ProviderError
from apex.domain.market import AssetClass, InstrumentSpec, Timeframe
from apex.marketdata.base import MarketDataProvider, normalise_bars
from apex.marketdata.continuous import Roll, stitch
from apex.marketdata.providers.csv_file import CsvFileProvider


class ContinuousFuturesProvider(MarketDataProvider):
    def __init__(self, root: str | Path, method: str = "difference",
                 assume_tz: str = "UTC") -> None:
        self.root, self.method = Path(root), method
        self.name = "csv-cont" if method == "difference" else f"csv-cont-{method}"
        self._csv = CsvFileProvider(root, assume_tz)
        self.last_rolls: list[Roll] = []

    def files(self, spec: InstrumentSpec, timeframe: Timeframe) -> list[Path]:
        folder = self.root / spec.symbol
        return sorted(folder.glob(f"*_{timeframe.value}.csv")) if folder.is_dir() else []

    def supports(self, spec: InstrumentSpec, timeframe: Timeframe) -> bool:
        return spec.asset_class is AssetClass.FUTURES and bool(self.files(spec, timeframe))

    def _fetch(self, spec, timeframe, start, end) -> pd.DataFrame:  # noqa: ANN001
        suffix = f"_{timeframe.value}"
        contracts = []
        for path in self.files(spec, timeframe):
            df = normalise_bars(self._csv.read(path)).dropna(subset=["close"])
            if not df.empty:
                contracts.append((path.stem.removesuffix(suffix), df))
        contracts.sort(key=lambda c: (c[1]["ts"].iloc[-1], c[0]))
        if not contracts:
            raise ProviderError(f"no bars in {self.root / spec.symbol}")
        frame, self.last_rolls = stitch(contracts, self.method)
        return frame
