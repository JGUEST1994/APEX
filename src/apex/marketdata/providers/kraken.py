"""Kraken public OHLC endpoint (crypto spot). No API key needed.

Kraken returns at most the latest 720 bars per interval, whatever ``since`` asks for, so
this adapter suits incremental updates (``apex update``) rather than deep backfills. Use
Binance or CSV exports for long history.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pandas as pd

from apex.core.errors import ProviderError
from apex.domain.market import AssetClass, InstrumentSpec, Timeframe
from apex.marketdata.base import MarketDataProvider

_TF = {Timeframe.M1: 1, Timeframe.M5: 5, Timeframe.M15: 15, Timeframe.M30: 30,
       Timeframe.H1: 60, Timeframe.D1: 1440}


def kraken_pair(spec: InstrumentSpec) -> str:
    base = spec.base_currency or spec.symbol.removesuffix(spec.quote_currency or "")
    return ("XBT" if base == "BTC" else base) + (spec.quote_currency or "USD")


class KrakenSpotProvider(MarketDataProvider):
    name = "kraken"

    def __init__(self, *, base_url: str = "https://api.kraken.com",
                 client: httpx.Client | None = None, max_pages: int = 50) -> None:
        self.client = client or httpx.Client(base_url=base_url, timeout=30.0)
        self.max_pages = max_pages

    def supports(self, spec: InstrumentSpec, timeframe: Timeframe) -> bool:
        return spec.asset_class is AssetClass.CRYPTO and timeframe in _TF

    def _fetch(self, spec, timeframe, start: datetime, end: datetime) -> pd.DataFrame:  # noqa: ANN001
        rows: list[list] = []
        since = int(start.timestamp()) - 1
        end_s = end.timestamp()
        for _ in range(self.max_pages):
            try:
                r = self.client.get("/0/public/OHLC", params={
                    "pair": kraken_pair(spec), "interval": _TF[timeframe], "since": since})
                r.raise_for_status()
                body = r.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"kraken OHLC request failed: {type(exc).__name__}") from None
            if body.get("error"):
                raise ProviderError(f"kraken error: {body['error']}")
            result = body.get("result") or {}
            series = next((v for k, v in result.items() if k != "last"), [])
            new = [row for row in series if int(row[0]) > since]
            rows.extend(new)
            last = int(result.get("last") or 0)
            if not new or last <= since or last >= end_s:
                break
            since = last
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vwap",
                                         "volume", "count"])
        if df.empty:
            return df
        df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="s", utc=True)
        # the newest Kraken bar is still forming; drop it so only completed bars are stored
        now = pd.Timestamp.now(tz="UTC")
        df = df[df["ts"] + pd.Timedelta(minutes=_TF[timeframe]) <= now]
        return df.drop_duplicates("ts", keep="last")[["ts", "open", "high", "low", "close",
                                                      "volume"]]
