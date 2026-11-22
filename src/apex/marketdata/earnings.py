"""Earnings-release dates from the SEC's official EDGAR API (free, US-listed companies).

A company's results are announced in an 8-K filing with item 2.02 ("Results of Operations
and Financial Condition"). The SEC's fair-access rules ask every automated client to send a
User-Agent naming who is calling, with a contact email, and to stay under 10 requests per
second; APEX makes two requests per symbol, plus one for each older page of the company's
filings (a few for a large company), and waits at least 0.125 s between requests. Set
``APEX_SEC_USER_AGENT`` to e.g. ``"Jane Smith jane@example.com"``. Read-only; nothing is
sent except the ticker lookup.

The SEC lists a company's latest 1,000 or so filings first and older ones on further pages;
every page is read, so the dates reach back to 2003. The item number changed once: results
were item 12 of Form 8-K from March 2003 until the items were renumbered on 23 August 2004.

Each filing becomes an *anchor date*: the first trading session that can react to the news.
A filing accepted before 09:30 New York time anchors that day; any later one anchors the
next day (weekends roll forward to the next bar automatically in the strategy).
"""

from __future__ import annotations

from datetime import time, timedelta
from time import monotonic, sleep

import httpx
import pandas as pd

from apex.core.errors import ProviderError

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
OLDER_URL = "https://data.sec.gov/submissions/{name}"
OPEN_NY = time(9, 30)
ITEM_12_UNTIL = pd.Timestamp("2004-08-23", tz="America/New_York")  # then renumbered 2.02


class SecEarningsDates:
    def __init__(self, user_agent: str, client: httpx.Client | None = None,
                 min_interval: float = 0.125) -> None:
        if not user_agent or "@" not in user_agent:
            raise ProviderError("the SEC asks for a User-Agent with your name and email: "
                                "set APEX_SEC_USER_AGENT=\"Your Name you@example.com\"")
        self.client = client or httpx.Client(timeout=30.0)
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self._min_interval = min_interval  # the SEC allows at most 10 requests a second
        self._last: float | None = None

    def _get(self, url: str) -> dict:
        if self._last is not None:
            wait = self._last + self._min_interval - monotonic()
            if wait > 0:
                sleep(wait)
        try:
            r = self.client.get(url, headers=self._headers)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "")
            raise ProviderError(f"SEC EDGAR request failed: {type(exc).__name__} {status}") \
                from None
        finally:
            self._last = monotonic()

    def cik(self, ticker: str) -> int:
        want = ticker.upper().replace(".", "-")
        for row in self._get(TICKERS_URL).values():
            if str(row.get("ticker", "")).upper() == want:
                return int(row["cik_str"])
        raise ProviderError(f"{ticker} is not in the SEC's list of company tickers")

    def releases(self, ticker: str) -> list[pd.Timestamp]:
        """Acceptance times of 8-K filings that report results (item 2.02, or item 12 before
        the renumbering), oldest first, as New York time, from every page of the company's
        filings. EDGAR writes them with a trailing "Z"; reading the clock time as New York
        time is the cautious choice: it can only move an anchor later, never earlier."""
        filings = self._get(SUBMISSIONS_URL.format(cik=self.cik(ticker)))["filings"]
        pages = [filings["recent"]] + [self._get(OLDER_URL.format(name=f["name"]))
                                       for f in filings.get("files", [])]
        out = set()
        for page in pages:
            for form, items, accepted in zip(page.get("form", []), page.get("items", []),
                                             page.get("acceptanceDateTime", []), strict=False):
                if form not in ("8-K", "8-K/A"):
                    continue
                t = pd.Timestamp(str(accepted).rstrip("Z")).tz_localize("America/New_York")
                found = str(items).split(",")
                if "2.02" in found or ("12" in found and t < ITEM_12_UNTIL):
                    out.add(t)
        return sorted(out)


def anchor_dates(releases: list[pd.Timestamp]) -> list[str]:
    """First session date that can react to each release (see module docstring)."""
    days = set()
    for t in releases:
        ny = t.tz_convert("America/New_York")
        day = ny.date() if ny.time() < OPEN_NY else ny.date() + timedelta(days=1)
        days.add(day.isoformat())
    return sorted(days)
