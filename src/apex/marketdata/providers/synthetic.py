"""Deterministic synthetic data for tests, demos and offline development.

Uses geometric Brownian motion seeded from (symbol, timeframe, seed) so the same request
always returns identical bars. It respects the instrument's trading calendar.

By default each bar opens at the previous close (no gaps). ``gap_vol`` > 0 adds a random
jump (log-return standard deviation ``gap_vol``) at the first bar after every break in the
schedule, such as an overnight or weekend close, so gap strategies have something to test on.
``order_flow=True`` also adds ``buy_volume`` (aggressive buying), leaning with each bar's
direction, so order-flow strategies have something to test on.
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

    def __init__(self, seed: int = 7, start_price: float = 100.0, annual_vol: float = 0.2,
                 gap_vol: float = 0.0, order_flow: bool = False):
        self.seed, self.start_price, self.annual_vol = seed, start_price, annual_vol
        self.gap_vol, self.order_flow = gap_vol, order_flow

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
        if self.gap_vol > 0 and n > 1:
            # Drawn from a separate stream so the default (no-gap) bars stay unchanged.
            gap_rng = np.random.default_rng(rng.integers(0, 2**63))
            step = pd.Series(pd.DatetimeIndex(times)).diff()
            after_break = (step > pd.Timedelta(timeframe.delta)).to_numpy()
            jumps = np.where(after_break, gap_rng.normal(0.0, self.gap_vol, size=n), 0.0)
            log_open = np.log(self.start_price) + np.cumsum(jumps) + np.concatenate(
                [[0.0], np.cumsum(rets)[:-1]])
            open_ = np.exp(log_open)
            close = np.exp(log_open + rets)
        wiggle = np.abs(rng.normal(0.0, sigma / 2, size=n))
        high = np.maximum(open_, close) * (1 + wiggle)
        low = np.minimum(open_, close) * (1 - wiggle)
        volume = rng.integers(100, 10_000, size=n).astype(float)
        out = pd.DataFrame({"ts": times, "open": open_, "high": high, "low": low,
                            "close": close, "volume": volume})
        if self.order_flow:
            # Separate stream so the price bars stay identical with or without it.
            flow_rng = np.random.default_rng(rng.integers(0, 2**63))
            lean = np.tanh(np.log(close / open_) / sigma) * 0.2 if n else close
            share = np.clip(0.5 + lean + flow_rng.normal(0.0, 0.1, size=n), 0.0, 1.0)
            out["buy_volume"] = volume * share
        return out
