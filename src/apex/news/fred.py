"""FRED and ALFRED (St. Louis Fed): economic release calendar and macro/market series.

Free API key (``APEX_FRED_API_KEY``). FRED only accepts the key as a URL parameter, so error
messages never include the URL. Requests are spaced 0.5 s apart (FRED allows about 120 a
minute). Observations are read with their ALFRED vintages (``realtime_start``: the first day
each value was published), so revised figures never leak into the past.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from time import monotonic

import httpx

from apex.core.errors import ProviderError

BASE = "https://api.stlouisfed.org/fred/"
ALL_TIME = {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31"}


@dataclass(frozen=True)
class Release:
    name: str  # FRED's release name, matched without regard to case
    category: str  # fed, jobs, inflation, growth, sentiment
    label: str


@dataclass(frozen=True)
class Series:
    id: str
    label: str
    unit: str


RELEASES = [
    Release("FOMC Press Release", "fed", "Fed interest-rate decision"),
    Release("Employment Situation", "jobs", "US jobs report"),
    Release("Unemployment Insurance Weekly Claims Report", "jobs", "Weekly jobless claims"),
    Release("Job Openings and Labor Turnover Survey", "jobs", "Job openings (JOLTS)"),
    Release("Consumer Price Index", "inflation", "Consumer prices (CPI)"),
    Release("Producer Price Index", "inflation", "Producer prices (PPI)"),
    Release("Personal Income and Outlays", "inflation", "Spending and PCE inflation"),
    Release("Gross Domestic Product", "growth", "GDP"),
    Release("Advance Monthly Sales for Retail and Food Services", "growth", "Retail sales"),
    Release("Industrial Production and Capacity Utilization", "growth", "Industrial production"),
    Release("Surveys of Consumers", "sentiment", "Consumer sentiment (Michigan)"),
]

SERIES = [
    Series("VIXCLS", "Fear index (VIX)", "points"),
    Series("DGS10", "10-year Treasury yield", "%"),
    Series("DGS2", "2-year Treasury yield", "%"),
    Series("T10Y2Y", "Yield curve (10y minus 2y)", "% points"),
    Series("DFF", "Fed funds rate", "%"),
    Series("BAA10Y", "Company borrowing spread (Baa minus 10y)", "% points"),
    Series("STLFSI4", "Financial stress index (St. Louis Fed)", "index"),
    Series("NFCI", "Financial conditions (Chicago Fed, above 0 = tight)", "index"),
    Series("ICSA", "Weekly jobless claims", "people"),
    Series("UNRATE", "Unemployment rate", "%"),
    Series("CPIAUCSL", "Consumer price index", "index"),
    Series("DCOILWTICO", "Oil price (WTI)", "$/barrel"),
    Series("DTWEXBGS", "US dollar index (broad)", "index"),
]
SERIES_BY_ID = {s.id: s for s in SERIES}


class FredClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = _time.sleep, gap: float = 0.5) -> None:
        if not api_key:
            raise ProviderError("FRED needs a free key: set APEX_FRED_API_KEY "
                                "(fred.stlouisfed.org/docs/api/api_key.html)")
        self._key = api_key
        self.client = client or httpx.Client(timeout=60.0)
        self.sleep, self.gap = sleep, gap
        self._last: float | None = None

    def _get(self, path: str, **params: object) -> dict:
        for attempt in (1, 2):
            if self._last is not None and (wait := self._last + self.gap - monotonic()) > 0:
                self.sleep(wait)
            try:
                r = self.client.get(BASE + path, params={**params, "api_key": self._key,
                                                         "file_type": "json"},
                                    headers={"User-Agent": "APEX-research/1.0"})
            except httpx.HTTPError as exc:
                raise ProviderError(f"FRED {path}: {type(exc).__name__}") from None
            finally:
                self._last = monotonic()
            if r.status_code == 429 and attempt == 1:
                self.sleep(20.0)
                continue
            if r.status_code >= 400:
                raise ProviderError(f"FRED {path}: HTTP {r.status_code}")  # no URL: it has the key
            try:
                return r.json()
            except ValueError:
                raise ProviderError(f"FRED {path}: not JSON") from None
        raise ProviderError(f"FRED {path}: still rate-limited")  # pragma: no cover

    def release_ids(self) -> dict[str, int]:
        """FRED release name (lower case) -> id."""
        out: dict[str, int] = {}
        offset = 0
        while True:
            page = self._get("releases", limit=1000, offset=offset)
            rows = page.get("releases", [])
            out.update({str(r["name"]).strip().lower(): int(r["id"]) for r in rows})
            offset += len(rows)
            if not rows or offset >= int(page.get("count", 0)):
                return out

    def release_dates(self, release_id: int) -> list[date]:
        """Every date the release came out, past and scheduled."""
        page = self._get("release/dates", release_id=release_id, limit=10000,
                         sort_order="asc", include_release_dates_with_no_data="true",
                         **ALL_TIME)
        return sorted({date.fromisoformat(r["date"]) for r in page.get("release_dates", [])})

    def observations(self, series_id: str, start: date | None = None) -> list[dict]:
        """All vintages: rows with date, value (float) and vintage (realtime_start)."""
        params = {"series_id": series_id, "limit": 100000, **ALL_TIME}
        if start is not None:
            params["observation_start"] = start.isoformat()
        rows = self._get("series/observations", **params).get("observations", [])
        out = []
        for r in rows:
            try:
                v = float(r["value"])
            except (TypeError, ValueError):  # "." means no value that day
                continue
            out.append({"date": date.fromisoformat(r["date"]), "value": v,
                        "vintage": date.fromisoformat(r["realtime_start"])})
        return out
