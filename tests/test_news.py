"""News and official-release feeds: parsing, point-in-time storage, polite fetching,
the scheduler job and the dashboard endpoints. All HTTP is mocked; nothing reaches the
network."""

from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from apex.api.app import create_app
from apex.core.config import Settings
from apex.core.errors import ProviderError
from apex.db.models import NewsItem
from apex.db.session import make_engine, make_session_factory
from apex.news.fetch import fetch_feeds
from apex.news.parse import Entry, FeedError, parse_feed, parse_time
from apex.news.sources import FEEDS, Fetcher
from apex.news.store import known_before_bar, label, latest, store_entries
from apex.scheduler import Scheduler, parse_jobs
from tests.conftest import utc

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Fed</title>
<item><title>Federal Reserve issues &lt;b&gt;FOMC&lt;/b&gt; statement</title>
<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20251210a.htm</link>
<guid>monetary20251210a</guid><description>&lt;p&gt;The Committee  decided&lt;/p&gt;</description>
<pubDate>Wed, 10 Dec 2025 19:00:00 GMT</pubDate></item>
<item><title>No date, skipped</title><guid>x</guid></item>
<item><title>Bad link</title><link>javascript:alert(1)</link><guid>y</guid>
<pubDate>Wed, 10 Dec 2025 20:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Latest Filings</title>
<entry><title>8-K - Apple Inc. (0000320193) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/x-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2025-10-30 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-25-000071</summary>
<updated>2025-10-30T16:30:43-04:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0000320193-25-000071</id></entry>
<entry><title>8-K - Unknown Co (0009999999) (Filer)</title>
<link rel="alternate" href="https://www.sec.gov/y"/><updated>2025-10-30T16:31:00-04:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0009999999-25-000001</id></entry>
</feed>"""

TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
           "1": {"cik_str": 320193, "ticker": "AAPL-W", "title": "Apple Inc."}}

ALPACA = {"news": [{"id": 24843171, "headline": "Apple shares rise after results",
                    "summary": "Shares <i>rose</i>.", "author": "Benzinga Newsdesk",
                    "created_at": "2025-10-30T20:45:00Z", "updated_at": "2025-10-30T20:46:00Z",
                    "url": "https://www.benzinga.com/news/1", "symbols": ["aapl", "QQQ"],
                    "source": "benzinga"}], "next_page_token": None}


def _fetcher(routes, *, sec_ua="Test Person test@example.com", alpaca=("KEYID123", "SECRET456")):
    calls, sleeps = [], []

    def handle(req):
        calls.append(req)
        for key, resp in routes.items():
            if key in str(req.url):
                return resp(req, len(calls)) if callable(resp) else resp
        return httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handle))
    return Fetcher(client, sec_user_agent=sec_ua, alpaca_keys=alpaca,
                   sleep=sleeps.append), calls, sleeps


# --------------------------------------------------------------------------- parsing
def test_rss_parsing_cleans_text_skips_undated_and_drops_unsafe_links():
    es = parse_feed(RSS)
    assert [e.source_id for e in es] == ["monetary20251210a", "y"]
    assert es[0].title == "Federal Reserve issues FOMC statement"
    assert es[0].summary == "The Committee decided"
    assert es[0].published_at == utc(2025, 12, 10, 19)
    assert es[0].url.startswith("https://www.federalreserve.gov/")
    assert es[1].url is None  # javascript: link removed


def test_atom_parsing_reads_offsets_as_utc():
    es = parse_feed(ATOM)
    assert es[0].published_at == utc(2025, 10, 30, 20, 30, 43)
    assert es[0].source_id.endswith("0000320193-25-000071")


def test_hostile_or_broken_feeds_are_refused():
    with pytest.raises(FeedError):
        parse_feed(b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss/>')
    with pytest.raises(FeedError):
        parse_feed(b"not xml")
    with pytest.raises(FeedError):
        parse_feed(b"<html><body/></html>")
    assert parse_time("garbage") is None
    assert parse_time("2025-01-02T03:04:05") == utc(2025, 1, 2, 3, 4, 5)  # no zone -> UTC


# --------------------------------------------------------------------------- storage
def _entry(title="Headline", when=utc(2025, 3, 3, 14), sid="a1", symbols=("AAPL",)):
    return Entry(sid, title, when, "https://example.com/a1", "Summary", list(symbols))


def test_store_versions_edits_and_never_changes_stored_rows(session):
    feed = FEEDS["alpaca-news"]
    seen1 = utc(2025, 3, 3, 14, 5)
    assert store_entries(session, feed, [_entry()], seen1) == {"new": 1, "changed": 0,
                                                               "unchanged": 0}
    assert store_entries(session, feed, [_entry()], seen1 + timedelta(minutes=15))["unchanged"] == 1
    seen2 = utc(2025, 3, 3, 16)
    assert store_entries(session, feed, [_entry("Headline (corrected)")], seen2)["changed"] == 1
    session.commit()
    rows = session.scalars(select(NewsItem).order_by(NewsItem.version)).all()
    assert [(r.version, r.title, r.first_seen_at) for r in rows] == [
        (1, "Headline", seen1), (2, "Headline (corrected)", seen2)]
    assert rows[0].known_at == seen1 and rows[1].known_at == seen2
    assert [x.symbol for x in rows[1].symbols] == ["AAPL"]
    shown = latest(session)
    assert [r.title for r in shown] == ["Headline (corrected)"]  # latest version only
    assert latest(session, symbol="aapl")[0].id == rows[1].id
    assert latest(session, symbol="MSFT") == []


def test_store_rejects_naive_time(session):
    with pytest.raises(ValueError):
        store_entries(session, FEEDS["fed-press"], [_entry()], utc(2025, 1, 1).replace(tzinfo=None))


def test_no_look_ahead_item_only_reaches_bars_opening_after_known_at(session):
    """The key point-in-time rule: published 14:00 but first seen 14:20, so the 14:00 bar
    and the bar opening exactly at 14:20 can't see it; the next one can."""
    store_entries(session, FEEDS["fed-press"], [_entry(symbols=())], utc(2025, 3, 3, 14, 20))
    # a backfilled item claims an earlier publish time but was only seen days later
    store_entries(session, FEEDS["fed-press"],
                  [_entry("Old release", utc(2025, 3, 1, 9), "old", ())], utc(2025, 3, 5, 9))
    session.commit()
    assert known_before_bar(session, utc(2025, 3, 3, 14)) == []
    assert known_before_bar(session, utc(2025, 3, 3, 14, 20)) == []
    assert [i.title for i in known_before_bar(session, utc(2025, 3, 3, 15))] == ["Headline"]
    assert [i.title for i in known_before_bar(session, utc(2025, 3, 4))] == ["Headline"]
    assert len(known_before_bar(session, utc(2025, 3, 6))) == 2
    with pytest.raises(ValueError):
        known_before_bar(session, utc(2025, 3, 6).replace(tzinfo=None))


def test_live_delayed_historical_labels(session):
    feed = FEEDS["fed-press"]
    pub = utc(2025, 3, 3, 14)
    for sid, lag in (("a", 5), ("b", 120), ("c", 3000)):
        store_entries(session, feed, [_entry(sid=sid, when=pub)], pub + timedelta(minutes=lag))
    session.commit()
    by = {i.source_id: label(i) for i in latest(session)}
    assert by == {"a": "live", "b": "delayed", "c": "historical"}


# --------------------------------------------------------------------------- fetching
def test_sec_feed_links_companies_to_tickers_and_names_the_caller():
    f, calls, _ = _fetcher({"company_tickers": httpx.Response(200, json=TICKERS),
                            "browse-edgar": httpx.Response(200, content=ATOM)})
    es = f.entries(FEEDS["sec-8k"])
    assert es[0].symbols == ["AAPL"] and es[0].extra["cik"] == [320193]
    assert es[1].symbols == []
    assert all(c.headers["User-Agent"] == "Test Person test@example.com" for c in calls)
    f.entries(FEEDS["sec-form4"])
    assert sum("company_tickers" in str(c.url) for c in calls) == 1  # ticker list kept


def test_sec_feeds_need_a_contact_user_agent():
    f, calls, _ = _fetcher({}, sec_ua="")
    with pytest.raises(ProviderError, match="APEX_SEC_USER_AGENT"):
        f.entries(FEEDS["sec-8k"])
    assert calls == []


def test_busy_server_is_retried_once_after_its_requested_wait():
    def busy_then_ok(req, n):
        return httpx.Response(429, headers={"Retry-After": "7"}) if n == 1 else \
            httpx.Response(200, content=RSS)
    f, calls, sleeps = _fetcher({"federalreserve.gov": busy_then_ok})
    assert len(f.entries(FEEDS["fed-press"])) == 2
    assert len(calls) == 2 and 7.0 in sleeps
    assert calls[0].headers["User-Agent"].startswith("APEX-research/")

    f, _, sleeps = _fetcher({"federalreserve.gov": httpx.Response(503, headers={"Retry-After": "9999"})})
    with pytest.raises(ProviderError, match="503"):
        f.entries(FEEDS["fed-press"])
    assert max(sleeps) <= 120  # capped


def test_alpaca_news_uses_keys_in_headers_only_and_tags_symbols():
    f, calls, _ = _fetcher({"data.alpaca.markets": httpx.Response(200, json=ALPACA)})
    [e] = f.entries(FEEDS["alpaca-news"])
    assert e.symbols == ["AAPL", "QQQ"] and e.summary == "Shares rose ."
    assert calls[0].headers["APCA-API-KEY-ID"] == "KEYID123" and "KEYID123" not in str(calls[0].url) and "SECRET456" not in str(calls[0].url)
    f2, _, _ = _fetcher({}, alpaca=None)
    with pytest.raises(ProviderError, match="ALPACA"):
        f2.entries(FEEDS["alpaca-news"])


def test_one_failing_feed_never_stops_the_others(session):
    f, _, _ = _fetcher({"federalreserve.gov": httpx.Response(200, content=RSS),
                        "ecb.europa.eu": httpx.Response(500)})
    r = fetch_feeds(session, f, ["ecb-press", "fed-press", "nope"], now=utc(2025, 12, 10, 19, 3))
    assert r["stored"] == {"fed-press": {"new": 2, "changed": 0, "unchanged": 0}}
    assert len(r["errors"]) == 2 and any("nope" in e for e in r["errors"])


# --------------------------------------------------------------------------- scheduler + API
def test_scheduler_news_job_and_dashboard_endpoints(db_url):
    f, _, _ = _fetcher({"company_tickers": httpx.Response(200, json=TICKERS),
                        "browse-edgar": httpx.Response(200, content=ATOM),
                        "federalreserve.gov": httpx.Response(200, content=RSS)})
    engine = make_engine(db_url)
    settings = Settings(_env_file=None, database_url=db_url)
    jobs = parse_jobs({"jobs": [{"name": "news", "type": "news",
                                 "feeds": ["sec-8k", "fed-press"], "every_minutes": 15}]})
    sch = Scheduler(jobs, settings, make_session_factory(engine), news_fetcher=f)
    [res] = sch.run_cycle(utc(2025, 12, 10, 19, 5))
    assert res["status"] == "ok" and res["stored"]["sec-8k"]["new"] == 2

    c = TestClient(create_app(settings, make_session_factory(engine)))
    rows = c.get("/news").json()
    assert rows[0]["source"] == "fed-press" and rows[0]["label"] == "live"
    assert {r["source_label"] for r in rows} >= {"SEC: company announcements (8-K)"}
    apple = c.get("/news?symbol=AAPL").json()
    assert len(apple) == 1 and apple[0]["label"] == "historical"  # seen weeks after filing
    assert apple[0]["url"].startswith("https://www.sec.gov/")
    assert all(r["url"] is None or r["url"].startswith("http") for r in rows)
    feeds = {r["name"]: r for r in c.get("/news/feeds").json()}
    assert feeds["sec-8k"]["stored"] == 2 and feeds["ecb-press"]["stored"] == 0
    page = c.get("/ui/").text
    assert '["news","News"]' in page and 'id="onews"' in page
    engine.dispose()
