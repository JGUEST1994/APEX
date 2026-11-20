"""Coinbase Exchange public candles endpoint (crypto spot). No API key needed.

``GET /products/{BASE-QUOTE}/candles`` returns at most 300 candles per request, newest
first, as ``[time, low, high, open, close, volume]``. This adapter walks forward in
300-candle windows, so unlike Kraken it can backfill long history (slowly: be gentle with
the public rate limit). Coinbase has no 30-minute candles.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import httpx
import pandas as pd

from apex.core.errors import ProviderError
from apex.domain.market import AssetClass, InstrumentSpec, Timeframe
from apex.marketdata.base import MarketDataProvider

_GRAN = {Timeframe.M1: 60, Timeframe.M5: 300, Timeframe.M15: 900, Timeframe.H1: 3600,
         Timeframe.D1: 86400}
_PAGE = 300


def coinbase_product(spec: InstrumentSpec) -> str:
    quote = spec.quote_currency or "USD"
    base = spec.base_currency or spec.symbol.removesuffix(quote)
    return f"{base}-{quote}"


class CoinbaseSpotProvider(MarketDataProvider):
    name = "coinbase"

    def __init__(self, *, base_url: str = "https://api.exchange.coinbase.com",
                 client: httpx.Client | None = None, max_pages: int = 500,
                 pause: float = 0.2) -> None:
        self.client = client or httpx.Client(base_url=base_url, timeout=30.0,
                                             headers={"User-Agent": "apex-research"})
        self.max_pages, self.pause = max_pages, pause

    def supports(self, spec: InstrumentSpec, timeframe: Timeframe) -> bool:
        return spec.asset_class is AssetClass.CRYPTO and timeframe in _GRAN

    def _fetch(self, spec, timeframe, start: datetime, end: datetime) -> pd.DataFrame:  # noqa: ANN001
        gran = _GRAN[timeframe]
        step = timedelta(seconds=gran * _PAGE)
        rows: list[list] = []
        lo, pages = start, 0
        while lo < end:
            if pages >= self.max_pages:
                raise ProviderError(f"coinbase: more than {self.max_pages} pages; narrow the "
                                    "date range")
            hi = min(lo + step, end)
            try:
                r = self.client.get(f"/products/{coinbase_product(spec)}/candles", params={
                    "granularity": gran, "start": lo.isoformat(),
                    # end is inclusive on Coinbase; stop one second short of the next window
                    "end": (hi - timedelta(seconds=1)).isoformat()})
                r.raise_for_status()
                body = r.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"coinbase candles request failed: {type(exc).__name__}"
                                    ) from None
            if isinstance(body, dict):  # {"message": "..."} on errors
                raise ProviderError(f"coinbase error: {body.get('message', body)}")
            rows.extend(body)
            lo, pages = hi, pages + 1
            if self.pause and lo < end:
                time.sleep(self.pause)
        df = pd.DataFrame(rows, columns=["ts", "low", "high", "open", "close", "volume"])
        if df.empty:
            return df
        df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="s", utc=True)
        # a candle that has not closed yet is still forming: keep completed bars only
        now = pd.Timestamp.now(tz="UTC")
        df = df[df["ts"] + pd.Timedelta(seconds=gran) <= now]
        df = df.sort_values("ts").drop_duplicates("ts", keep="last")
        return df[["ts", "open", "high", "low", "close", "volume"]]
