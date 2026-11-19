"""Polygon.io (now also branded Massive) aggregates for US equities/ETFs.

Needs ``APEX_POLYGON_API_KEY``. The key travels as a query parameter, so request URLs are
never logged or put into error messages. Read-only.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pandas as pd

from apex.core.errors import ProviderError
from apex.domain.market import AssetClass, InstrumentSpec, Timeframe
from apex.marketdata.base import MarketDataProvider

_TF = {Timeframe.M1: (1, "minute"), Timeframe.M5: (5, "minute"),
       Timeframe.M15: (15, "minute"), Timeframe.M30: (30, "minute"),
       Timeframe.H1: (1, "hour"), Timeframe.D1: (1, "day")}


class PolygonStocksProvider(MarketDataProvider):
    name = "polygon"

    def __init__(self, api_key: str, *, base_url: str = "https://api.polygon.io",
                 client: httpx.Client | None = None, max_pages: int = 1000) -> None:
        if not api_key:
            raise ProviderError("Polygon API key is required")
        self._key = api_key
        self.client = client or httpx.Client(base_url=base_url, timeout=30.0)
        self.max_pages = max_pages

    def supports(self, spec: InstrumentSpec, timeframe: Timeframe) -> bool:
        return spec.asset_class is AssetClass.EQUITY and timeframe in _TF

    def _get(self, url: str, params: dict) -> dict:
        try:
            r = self.client.get(url, params={**params, "apiKey": self._key})
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "")
            raise ProviderError(f"polygon request failed: {type(exc).__name__} {status}") \
                from None

    def _fetch(self, spec, timeframe, start: datetime, end: datetime) -> pd.DataFrame:  # noqa: ANN001
        mult, span = _TF[timeframe]
        url = (f"/v2/aggs/ticker/{spec.symbol}/range/{mult}/{span}/"
               f"{int(start.timestamp() * 1000)}/{int(end.timestamp() * 1000)}")
        params: dict = {"adjusted": "true", "sort": "asc", "limit": 50000}
        rows: list[dict] = []
        for _ in range(self.max_pages):
            body = self._get(url, params)
            rows.extend(body.get("results") or [])
            nxt = body.get("next_url")
            if not nxt:
                break
            url, params = nxt, {}  # next_url carries the cursor; the key is re-added
        else:
            raise ProviderError("polygon pagination exceeded max_pages")
        df = pd.DataFrame({"ts": [b["t"] for b in rows], "open": [b["o"] for b in rows],
                           "high": [b["h"] for b in rows], "low": [b["l"] for b in rows],
                           "close": [b["c"] for b in rows], "volume": [b.get("v", 0.0)
                                                                       for b in rows]})
        if df.empty:
            return df
        ts = pd.to_datetime(df["ts"], unit="ms", utc=True)
        if timeframe is Timeframe.D1:
            # daily aggregates are stamped at midnight New York; APEX uses 00:00 UTC of the
            # session date
            ts = ts.dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None) \
                .dt.tz_localize("UTC")
        df["ts"] = ts
        return df
