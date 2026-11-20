"""Tiingo: free US stock/ETF daily history and intraday forex, with your own free API key.

Needs ``APEX_TIINGO_API_KEY`` (sign up at tiingo.com; the free plan allows about 50
requests an hour and 1,000 a day, for personal use only, no redistribution). Tiingo's docs
say software where each user supplies their own token and does not redistribute the data
needs no licence from them. The key travels in the ``Authorization`` header, never in a URL,
and is never put into error messages. Read-only.

- US stocks/ETFs: daily bars from ``/tiingo/daily/<ticker>/prices``, split- and
  dividend-adjusted (``adjOpen`` ...), stamped 00:00 UTC of the trading day like every
  other APEX daily bar.
- Forex: 1m to 1h bars from ``/tiingo/fx/<pair>/prices`` (Tiingo marks this API beta;
  history from about 2020; no volume, stored as 0). Make daily FX bars with
  ``apex resample`` so they follow APEX's FX session calendar.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pandas as pd

from apex.core.errors import ProviderError
from apex.domain.market import AssetClass, InstrumentSpec, Timeframe
from apex.marketdata.base import MarketDataProvider

_FX_FREQ = {Timeframe.M1: "1min", Timeframe.M5: "5min", Timeframe.M15: "15min",
            Timeframe.M30: "30min", Timeframe.H1: "1hour"}
ROWS_PER_REQUEST = 5000  # keeps each intraday answer small; Tiingo does not page


class TiingoProvider(MarketDataProvider):
    name = "tiingo"

    def __init__(self, api_key: str, *, base_url: str = "https://api.tiingo.com",
                 client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ProviderError("Tiingo API key is required")
        self.client = client or httpx.Client(base_url=base_url, timeout=30.0)
        self._headers = {"Authorization": f"Token {api_key}"}

    def supports(self, spec: InstrumentSpec, timeframe: Timeframe) -> bool:
        if spec.asset_class is AssetClass.EQUITY:
            return timeframe is Timeframe.D1
        return spec.asset_class is AssetClass.FOREX and timeframe in _FX_FREQ

    def _get(self, url: str, params: dict) -> list:
        try:
            r = self.client.get(url, params=params, headers=self._headers)
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "")
            hint = " (check APEX_TIINGO_API_KEY)" if status in (401, 403) else \
                " (free-plan hourly limit reached?)" if status == 429 else ""
            raise ProviderError(f"tiingo request failed: {type(exc).__name__} {status}{hint}") \
                from None
        if isinstance(body, dict):  # Tiingo answers errors as {"detail": "..."}
            raise ProviderError(f"tiingo: {str(body.get('detail', body))[:200]}")
        return body

    def _fetch(self, spec, timeframe, start: datetime, end: datetime) -> pd.DataFrame:  # noqa: ANN001
        if spec.asset_class is AssetClass.EQUITY:
            rows = self._get(f"/tiingo/daily/{spec.symbol.replace('.', '-').lower()}/prices",
                             {"startDate": start.date().isoformat(),
                              "endDate": end.date().isoformat()})
            df = pd.DataFrame({"ts": [r["date"] for r in rows],
                               "open": [r["adjOpen"] for r in rows],
                               "high": [r["adjHigh"] for r in rows],
                               "low": [r["adjLow"] for r in rows],
                               "close": [r["adjClose"] for r in rows],
                               "volume": [r.get("adjVolume", 0.0) for r in rows]})
            if not df.empty:
                df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.normalize()
            return df
        rows = []
        step = timeframe.delta * ROWS_PER_REQUEST
        lo = start
        while lo < end:
            hi = min(lo + step, end)
            rows += self._get(f"/tiingo/fx/{spec.symbol.lower()}/prices",
                              {"startDate": lo.strftime("%Y-%m-%dT%H:%M:%S"),
                               "endDate": hi.strftime("%Y-%m-%dT%H:%M:%S"),
                               "resampleFreq": _FX_FREQ[timeframe]})
            lo = hi
        df = pd.DataFrame({"ts": [r["date"] for r in rows], "open": [r["open"] for r in rows],
                           "high": [r["high"] for r in rows], "low": [r["low"] for r in rows],
                           "close": [r["close"] for r in rows], "volume": 0.0})
        if not df.empty:
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            df = df.drop_duplicates("ts", keep="last")  # windows share their edge bar
        return df
