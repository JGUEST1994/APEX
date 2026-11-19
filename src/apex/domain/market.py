"""Core market domain types shared by every component.

These are plain, dependency-free value objects. Each asset class differs in calendar,
contract specification and cost model, so those live on ``InstrumentSpec`` rather than in
code branches scattered through the system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class AssetClass(str, Enum):
    FOREX = "forex"
    EQUITY = "equity"
    CRYPTO = "crypto"
    FUTURES = "futures"


class Timeframe(str, Enum):
    TICK = "tick"
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    D1 = "1d"

    @property
    def delta(self) -> timedelta:
        if self is Timeframe.TICK:
            raise ValueError("tick data has no fixed bar interval")
        return _DELTAS[self]

    @property
    def is_intraday(self) -> bool:
        return self is not Timeframe.D1


_DELTAS = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M30: timedelta(minutes=30),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.D1: timedelta(days=1),
}


@dataclass(frozen=True)
class CostModel:
    """Per-instrument transaction cost assumptions used by backtests and paper trading."""

    commission_per_unit: float = 0.0  # e.g. $ per contract or per share
    commission_bps: float = 0.0  # proportional commission, basis points of notional
    spread_bps: float = 0.0  # assumed half-spread paid on entry and exit
    slippage_bps: float = 0.0
    funding_bps_per_day: float = 0.0  # swaps / perpetual funding / margin interest


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str  # canonical APEX symbol, e.g. "EURUSD", "AAPL", "BTCUSDT", "ES"
    asset_class: AssetClass
    venue: str  # e.g. "FX", "NASDAQ", "BINANCE", "CME"
    calendar: str  # key into apex.marketdata.calendars.CALENDARS
    quote_currency: str
    base_currency: str | None = None
    tick_size: float = 0.01
    contract_multiplier: float = 1.0
    lot_size: float = 1.0
    costs: CostModel = field(default_factory=CostModel)


@dataclass(frozen=True)
class Bar:
    """An OHLCV bar. ``ts`` is the bar OPEN time, timezone-aware UTC."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("Bar.ts must be timezone-aware (UTC)")
