"""Alpaca Market Data v2 historical stock bars (US equities/ETFs).

Needs an Alpaca API key pair (free account; the free plan serves the IEX feed). Keys come
from ``APEX_ALPACA_KEY_ID`` / ``APEX_ALPACA_SECRET_KEY`` and are never logged. Read-only:
this adapter does not touch trading endpoints.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pandas as pd

from apex.core.errors import ProviderError
from apex.domain.market import AssetClass, InstrumentSpec, Timeframe
from apex.marketdata.base import MarketDataProvider

_TF = {Timeframe.M1: "1Min", Timeframe.M5: "5Min", Timeframe.M15: "15Min",
       Timeframe.M30: "30Min", Timeframe.H1: "1Hour", Timeframe.D1: "1Day"}


class AlpacaStocksProvider(MarketDataProvider):
    name = "alpaca"

    def __init__(self, key_id: str, secret_key: str, *, feed: str = "iex",
                 base_url: str = "https://data.alpaca.markets",
                 client: httpx.Client | None = None, max_pages: int = 1000) -> None:
        if not key_id or not secret_key:
            raise ProviderError("Alpaca key id and secret are required")
        self.feed = feed
        self.client = client or httpx.Client(base_url=base_url, timeout=30.0)
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self.max_pages = max_pages

    def supports(self, spec: InstrumentSpec, timeframe: Timeframe) -> bool:
        return spec.asset_class is AssetClass.EQUITY and timeframe in _TF

    def _fetch(self, spec, timeframe, start: datetime, end: datetime) -> pd.DataFrame:  # noqa: ANN001
        rows: list[dict] = []
        token: str | None = None
        for _ in range(self.max_pages):
            params = {"timeframe": _TF[timeframe], "start": start.isoformat(),
                      "end": end.isoformat(), "limit": 10000, "adjustment": "all",
                      "feed": self.feed}
            if token:
                params["page_token"] = token
            try:
                r = self.client.get(f"/v2/stocks/{spec.symbol}/bars", params=params,
                                    headers=self._headers)
                r.raise_for_status()
                body = r.json()
            except (httpx.HTTPError, ValueError) as exc:
                # never include headers in the message
                raise ProviderError(f"alpaca bars request failed: {type(exc).__name__}: "
                                    f"{getattr(getattr(exc, 'response', None), 'status_code', '')}"
                                    ) from None
            rows.extend(body.get("bars") or [])
            token = body.get("next_page_token")
            if not token:
                break
        else:
            raise ProviderError("alpaca pagination exceeded max_pages")
        df = pd.DataFrame({"ts": [b["t"] for b in rows], "open": [b["o"] for b in rows],
                           "high": [b["h"] for b in rows], "low": [b["l"] for b in rows],
                           "close": [b["c"] for b in rows], "volume": [b["v"] for b in rows]})
        if timeframe is Timeframe.D1 and not df.empty:
            # Alpaca stamps daily bars at 04:00/05:00 UTC (midnight New York); APEX uses
            # 00:00 UTC of the session date.
            ts = pd.to_datetime(df["ts"], utc=True)
            df["ts"] = ts.dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None) \
                .dt.tz_localize("UTC")
        return df
