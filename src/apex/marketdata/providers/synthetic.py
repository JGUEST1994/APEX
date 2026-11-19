"""Deterministic synthetic data for tests, demos and offline development.

Uses geometric Brownian motion seeded from (symbol, timeframe, seed) so the same request
always returns identical bars. It respects the instrument's trading calendar.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import numpy as np
import pandas as pd

from apex.domain.market import InstrumentSpec, Timeframe
from apex.marketdata.base import MarketDataProvider
from apex.marketdata.calendars import get_calendar


class SyntheticProvider(MarketDataProvider):
    name = "synthetic"

    def __init__(self, seed: int = 7, start_price: float = 100.0, annual_vol: float = 0.2):
        self.seed, self.start_price, self.annual_vol = seed, start_price, annual_vol

    def supports(self, spec: InstrumentSpec, timeframe: Timeframe) -> bool:
        return timeframe is not Timeframe.TICK

    def _fetch(self, spec, timeframe, start: datetime, end: datetime) -> pd.DataFrame:
        times = list(get_calendar(spec.calendar).expected_bars(start, end, timeframe))
        key = f"{spec.symbol}|{timeframe.value}|{self.seed}|{start.isoformat()}".encode()
        rng = np.random.default_rng(int.from_bytes(hashlib.sha256(key).digest()[:8], "big"))
        n = len(times)
        dt_years = timeframe.delta.total_seconds() / (365.25 * 86400)
        sigma = self.annual_vol * np.sqrt(dt_years)
        rets = rng.normal(0.0, sigma, size=n)
        close = self.start_price * np.exp(np.cumsum(rets))
        open_ = np.concatenate([[self.start_price], close[:-1]]) if n else close
        wiggle = np.abs(rng.normal(0.0, sigma / 2, size=n))
        high = np.maximum(open_, close) * (1 + wiggle)
        low = np.minimum(open_, close) * (1 - wiggle)
        volume = rng.integers(100, 10_000, size=n).astype(float)
        return pd.DataFrame({"ts": times, "open": open_, "high": high, "low": low,
                             "close": close, "volume": volume})
