"""Earnings-release dates from the SEC's official EDGAR API (free, US-listed companies).

A company's results are announced in an 8-K filing with item 2.02 ("Results of Operations
and Financial Condition"). The SEC's fair-access rules ask every automated client to send a
User-Agent naming who is calling, with a contact email, and to stay under 10 requests per
second; APEX makes two requests per symbol. Set ``APEX_SEC_USER_AGENT`` to e.g.
``"Jane Smith jane@example.com"``. Read-only; nothing is sent except the ticker lookup.

Each filing becomes an *anchor date*: the first trading session that can react to the news.
A filing accepted before 09:30 New York time anchors that day; any later one anchors the
next day (weekends roll forward to the next bar automatically in the strategy).
"""

from __future__ import annotations

from datetime import time, timedelta

import httpx
import pandas as pd

from apex.core.errors import ProviderError

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
OPEN_NY = time(9, 30)


class SecEarningsDates:
    def __init__(self, user_agent: str, client: httpx.Client | None = None) -> None:
        if not user_agent or "@" not in user_agent:
            raise ProviderError("the SEC asks for a User-Agent with your name and email: "
                                "set APEX_SEC_USER_AGENT=\"Your Name you@example.com\"")
        self.client = client or httpx.Client(timeout=30.0)
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}

    def _get(self, url: str) -> dict:
        try:
            r = self.client.get(url, headers=self._headers)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "")
            raise ProviderError(f"SEC EDGAR request failed: {type(exc).__name__} {status}") \
                from None

    def cik(self, ticker: str) -> int:
        want = ticker.upper().replace(".", "-")
        for row in self._get(TICKERS_URL).values():
            if str(row.get("ticker", "")).upper() == want:
                return int(row["cik_str"])
        raise ProviderError(f"{ticker} is not in the SEC's list of company tickers")

    def releases(self, ticker: str) -> list[pd.Timestamp]:
        """Acceptance times of 8-K filings that include item 2.02, oldest first, as New York
        time. EDGAR writes them with a trailing "Z"; reading the clock time as New York time
        is the cautious choice: it can only move an anchor later, never earlier."""
        recent = self._get(SUBMISSIONS_URL.format(cik=self.cik(ticker)))["filings"]["recent"]
        out = []
        for form, items, accepted in zip(recent["form"], recent["items"],
                                         recent["acceptanceDateTime"], strict=False):
            if form in ("8-K", "8-K/A") and "2.02" in str(items).split(","):
                out.append(pd.Timestamp(str(accepted).rstrip("Z")).tz_localize(
                    "America/New_York"))
        return sorted(set(out))


def anchor_dates(releases: list[pd.Timestamp]) -> list[str]:
    """First session date that can react to each release (see module docstring)."""
    days = set()
    for t in releases:
        ny = t.tz_convert("America/New_York")
        day = ny.date() if ny.time() < OPEN_NY else ny.date() + timedelta(days=1)
        days.add(day.isoformat())
    return sorted(days)
