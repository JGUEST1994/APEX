"""Binance public market data (spot klines). No API key needed for historical bars.

Read-only: this adapter never touches order endpoints. Requests are paginated (max 1000
bars per call) and the HTTP client is injectable so tests run without network access.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pandas as pd

from apex.core.errors import ProviderError
from apex.domain.market import AssetClass, InstrumentSpec, Timeframe
from apex.marketdata.base import MarketDataProvider

_INTERVALS = {Timeframe.M1: "1m", Timeframe.M5: "5m", Timeframe.M15: "15m",
              Timeframe.M30: "30m", Timeframe.H1: "1h", Timeframe.D1: "1d"}


class BinanceSpotProvider(MarketDataProvider):
    name = "binance"
    LIMIT = 1000

    def __init__(self, base_url: str = "https://data-api.binance.vision",
                 client: httpx.Client | None = None, max_pages: int = 500) -> None:
        self.client = client or httpx.Client(base_url=base_url, timeout=20.0)
        self.max_pages = max_pages

    def supports(self, spec: InstrumentSpec, timeframe: Timeframe) -> bool:
        return spec.asset_class is AssetClass.CRYPTO and timeframe in _INTERVALS

    def _fetch(self, spec, timeframe, start: datetime, end: datetime) -> pd.DataFrame:
        rows: list[list] = []
        cursor = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        for _ in range(self.max_pages):
            params = {"symbol": spec.symbol, "interval": _INTERVALS[timeframe],
                      "startTime": cursor, "endTime": end_ms - 1, "limit": self.LIMIT}
            try:
                resp = self.client.get("/api/v3/klines", params=params)
                resp.raise_for_status()
                page = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"binance klines request failed: {exc}") from exc
            if not isinstance(page, list):
                raise ProviderError(f"unexpected binance response: {str(page)[:200]}")
            if not page:
                break
            rows.extend(page)
            last_open = int(page[-1][0])
            if len(page) < self.LIMIT or last_open + 1 >= end_ms:
                break
            cursor = last_open + 1
        else:
            raise ProviderError("binance pagination exceeded max_pages")
        return pd.DataFrame(
            {
                "ts": [datetime.fromtimestamp(int(r[0]) / 1000, UTC) for r in rows],
                "open": [r[1] for r in rows], "high": [r[2] for r in rows],
                "low": [r[3] for r in rows], "close": [r[4] for r in rows],
                "volume": [r[5] for r in rows],
                # taker buy base volume: the part of volume from aggressive buyers
                "buy_volume": [r[9] for r in rows],
            }
        )
