"""Import price files exported by hand from Dukascopy's free Historical Data Export page.

Dukascopy's website terms forbid automated downloading ("scraper", "robot", "bot" ...)
without their written consent, so APEX does not fetch from Dukascopy. Downloading files
yourself for personal, non-commercial use is allowed; save each one as
``<root>/<SYMBOL>_<timeframe>.csv`` (e.g. ``data/csv/EURUSD_1h.csv``) and ingest with
``--provider dukascopy-csv``.

Handles the export's quirks: day-first timestamps (``01.03.2024 00:00:00.000``) in a
``Gmt time``, ``Time (UTC)``, ``Time (EET)`` or ``Local time`` column (the last with a
``GMT+0200`` style suffix), and the flat zero-volume filler bars Dukascopy writes for
hours when the market was closed (weekends), which are dropped.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from apex.core.errors import ProviderError
from apex.marketdata.providers.csv_file import CsvFileProvider

FORMAT = "%d.%m.%Y %H:%M:%S.%f"


def _parse_time(col: str, values: pd.Series) -> pd.Series:
    text = values.astype(str).str.strip()
    if col in ("gmt time", "time (utc)", "time (gmt)"):
        return pd.to_datetime(text, format=FORMAT).dt.tz_localize("UTC")
    if col == "time (eet)":
        # Dukascopy's EET follows European summer time (UTC+2 / UTC+3)
        local = pd.to_datetime(text, format=FORMAT)
        return local.dt.tz_localize("Europe/Athens", ambiguous="NaT", nonexistent="NaT") \
            .dt.tz_convert("UTC")
    if col == "local time":
        parts = text.str.extract(r"^(.*?)\s*GMT([+-])(\d{2})(\d{2})$")
        if parts.isna().any().any():
            raise ProviderError("'Local time' values need a GMT+hhmm suffix; "
                                "export with 'GMT' time instead")
        base = pd.to_datetime(parts[0], format=FORMAT)
        sign = parts[1].map({"+": 1, "-": -1})
        offset = pd.to_timedelta(parts[2].astype(int) * 60 + parts[3].astype(int), unit="min")
        return (base - offset * sign).dt.tz_localize("UTC")
    raise ProviderError(f"unknown Dukascopy time column {col!r}")


class DukascopyCsvProvider(CsvFileProvider):
    name = "dukascopy-csv"

    def read(self, path: Path) -> pd.DataFrame:
        try:
            df = pd.read_csv(path)
        except Exception as exc:  # pragma: no cover - passthrough
            raise ProviderError(f"cannot read {path}: {exc}") from exc
        df.columns = [str(c).strip().lower() for c in df.columns]
        tcol = next((c for c in df.columns if "time" in c), None)
        if tcol is None:
            raise ProviderError(f"{path} has no Dukascopy time column (e.g. 'Gmt time')")
        try:
            ts = _parse_time(tcol, df[tcol])
        except ValueError as exc:
            raise ProviderError(f"{path}: unexpected time format ({exc}); "
                                "expected dd.mm.yyyy hh:mm:ss.fff") from exc
        df = df.drop(columns=[tcol]).assign(ts=ts).dropna(subset=["ts"])
        if "volume" in df:
            flat = (df["open"] == df["high"]) & (df["high"] == df["low"]) \
                & (df["low"] == df["close"])
            df = df[~(flat & (df["volume"] == 0))]
        return df.reset_index(drop=True)
