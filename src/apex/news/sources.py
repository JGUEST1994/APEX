"""The free feeds APEX reads, and how to fetch each one politely.

Every source here is official or licensed for this use (see docs/intelligence/sources.md,
which also lists the sources to avoid). Each request names APEX in its User-Agent; the SEC
also asks for a contact, so its feeds use ``APEX_SEC_USER_AGENT`` and are skipped without
it. A 429 or 503 answer is retried once after the wait the server asks for (at most two
minutes). Requests to one host are spaced out (SEC: 10 a second at most).
"""

from __future__ import annotations

import re
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlparse

import httpx

from apex.core.errors import ProviderError
from apex.news.parse import Entry, FeedError, clean_text, parse_feed, parse_time, safe_url

USER_AGENT = "APEX-research/1.0 (personal market research; polite polling)"
SEC_BROWSE = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}"
              "&owner=include&count=100&output=atom")
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
ALPACA_NEWS = "https://data.alpaca.markets/v1beta1/news"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_QUERY = ('("stock market" OR "federal reserve" OR "interest rates" OR inflation OR '
               'recession OR tariffs OR "wall street") sourcelang:english')
MAX_RETRY_WAIT = 120.0
_CIK = re.compile(r"\((\d{10})\)")


@dataclass(frozen=True)
class Feed:
    name: str
    label: str
    kind: str  # filing, central_bank, regulator, news
    url: str
    licence: str
    every_minutes: int = 15
    fmt: str = "feed"  # feed (RSS/Atom), sec (EDGAR + ticker links), alpaca, gdelt (JSON APIs)


SEC_TERMS = "SEC EDGAR public data; fair-access rules (10 req/s, contact User-Agent)"
FEEDS: dict[str, Feed] = {f.name: f for f in [
    Feed("sec-8k", "SEC: company announcements (8-K)", "filing",
         SEC_BROWSE.format(form="8-K"), SEC_TERMS, 10, "sec"),
    Feed("sec-form4", "SEC: insider trades (Form 4)", "filing",
         SEC_BROWSE.format(form="4"), SEC_TERMS, 30, "sec"),
    Feed("sec-13d", "SEC: activist stakes (13D)", "filing",
         SEC_BROWSE.format(form="SC%2013D"), SEC_TERMS, 60, "sec"),
    Feed("fed-press", "Federal Reserve: press releases", "central_bank",
         "https://www.federalreserve.gov/feeds/press_all.xml", "US government public domain"),
    Feed("fed-speeches", "Federal Reserve: speeches", "central_bank",
         "https://www.federalreserve.gov/feeds/speeches.xml", "US government public domain", 60),
    Feed("ecb-press", "ECB: press releases", "central_bank",
         "https://www.ecb.europa.eu/rss/press.html", "ECB public RSS, reuse with attribution"),
    Feed("boe-news", "Bank of England: news", "central_bank",
         "https://www.bankofengland.co.uk/rss/news", "Bank of England public RSS"),
    Feed("fda-press", "FDA: press releases", "regulator",
         "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml",
         "US government public domain", 60),
    Feed("alpaca-news", "Alpaca news (Benzinga)", "news", ALPACA_NEWS,
         "Alpaca market data terms: personal research use, no redistribution", 15, "alpaca"),
    Feed("gdelt-markets", "GDELT: world news about markets and the economy", "news", GDELT_DOC,
         "GDELT open data, free with attribution", 30, "gdelt"),
]}


class Fetcher:
    """HTTP for feeds: polite spacing per host, one retry on 429/503, never logs secrets."""

    def __init__(self, client: httpx.Client | None = None, *, sec_user_agent: str = "",
                 alpaca_keys: tuple[str, str] | None = None,
                 sleep: Callable[[float], None] = _time.sleep) -> None:
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True)
        self.sec_user_agent = sec_user_agent
        self.alpaca_keys = alpaca_keys
        self.sleep = sleep
        self._last: dict[str, float] = {}
        self._tickers: dict[int, str] | None = None

    def _get(self, url: str, *, headers: dict | None = None, params: dict | None = None,
             gap: float = 1.0) -> httpx.Response:
        host = urlparse(url).netloc
        for attempt in (1, 2):
            last = self._last.get(host)
            if last is not None and (wait := last + gap - monotonic()) > 0:
                self.sleep(wait)
            try:
                r = self.client.get(url, headers={"User-Agent": USER_AGENT, **(headers or {})},
                                    params=params)
            except httpx.HTTPError as exc:
                raise ProviderError(f"{host}: {type(exc).__name__}") from None
            finally:
                self._last[host] = monotonic()
            if r.status_code in (429, 503) and attempt == 1:
                self.sleep(_retry_after(r.headers.get("Retry-After")))
                continue
            if r.status_code >= 400:
                raise ProviderError(f"{host}: HTTP {r.status_code}")
            return r
        raise ProviderError(f"{host}: still busy after one retry")  # pragma: no cover

    # -- per-format readers
    def entries(self, feed: Feed) -> list[Entry]:
        if feed.fmt == "alpaca":
            return self._alpaca(feed)
        if feed.fmt == "sec":
            return self._sec(feed)
        if feed.fmt == "gdelt":
            return self._gdelt(feed)
        try:
            return parse_feed(self._get(feed.url).content)
        except FeedError as exc:
            raise ProviderError(f"{feed.name}: {exc}") from None

    def _sec_headers(self) -> dict:
        if not self.sec_user_agent or "@" not in self.sec_user_agent:
            raise ProviderError("SEC feeds need APEX_SEC_USER_AGENT=\"Your Name you@example.com\"")
        return {"User-Agent": self.sec_user_agent, "Accept-Encoding": "gzip, deflate"}

    def sec_tickers(self) -> dict[int, str]:
        """CIK -> ticker from the SEC's own list (one request, kept for this fetcher)."""
        if self._tickers is None:
            rows = self._get(SEC_TICKERS, headers=self._sec_headers(), gap=0.125).json()
            self._tickers = {}
            for row in rows.values():  # the first ticker listed for a company is its main one
                self._tickers.setdefault(int(row["cik_str"]), str(row["ticker"]).upper())
        return self._tickers

    def _sec(self, feed: Feed) -> list[Entry]:
        try:
            out = parse_feed(self._get(feed.url, headers=self._sec_headers(), gap=0.125).content)
        except FeedError as exc:
            raise ProviderError(f"{feed.name}: {exc}") from None
        tickers = self.sec_tickers()
        for e in out:
            ciks = [int(c) for c in _CIK.findall(e.title)]
            e.extra["cik"] = ciks
            e.symbols = sorted({tickers[c] for c in ciks if c in tickers})
        return out

    def _alpaca(self, feed: Feed) -> list[Entry]:
        if not self.alpaca_keys or not all(self.alpaca_keys):
            raise ProviderError("Alpaca news needs APEX_ALPACA_KEY_ID and APEX_ALPACA_SECRET_KEY")
        key, secret = self.alpaca_keys
        data = self._get(feed.url, headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
                         params={"limit": 50, "sort": "desc", "include_content": "false"},
                         gap=0.5).json()
        out = []
        for n in data.get("news", []):
            when = parse_time(n.get("created_at"))
            title = clean_text(n.get("headline"), 500)
            if not (when and title and n.get("id") is not None):
                continue
            out.append(Entry(str(n["id"]), title, when, safe_url(n.get("url")),
                             clean_text(n.get("summary")),
                             sorted({str(s).upper() for s in n.get("symbols") or []}),
                             {"updated_at": n.get("updated_at"), "publisher": n.get("source"),
                              "author": n.get("author")}))
        return out


    def _gdelt(self, feed: Feed) -> list[Entry]:
        """GDELT DOC API: English articles from the last day matching the market query.
        ``seendate`` is when GDELT's crawler first saw the article."""
        r = self._get(feed.url, params={"query": GDELT_QUERY, "mode": "ArtList",
                                        "format": "json", "maxrecords": 75, "timespan": "1d",
                                        "sort": "DateDesc"}, gap=6.0)
        try:
            arts = r.json().get("articles", [])
        except ValueError:  # GDELT answers plain text when it rejects a query
            raise ProviderError(f"{feed.name}: {r.text[:120]}") from None
        out = []
        for a in arts:
            url = safe_url(a.get("url"))
            when = parse_time(_gdelt_time(a.get("seendate")))
            title = clean_text(a.get("title"), 500)
            if url and when and title:
                out.append(Entry(url[:255], title, when, url, None, [],
                                 {"domain": a.get("domain"), "country": a.get("sourcecountry")}))
        return out


def _gdelt_time(s: str | None) -> str | None:
    """20251210T190000Z -> 2025-12-10T19:00:00Z"""
    if not s or len(s) < 15:
        return None
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[9:11]}:{s[11:13]}:{s[13:15]}Z"


def _retry_after(value: str | None) -> float:
    try:
        return min(MAX_RETRY_WAIT, max(1.0, float(value)))
    except (TypeError, ValueError):
        return 30.0
