"""FINRA short-sale volume, CFTC futures positioning and the GDELT feed. Mocked HTTP."""

from datetime import date

import httpx
from fastapi.testclient import TestClient

from apex.api.app import create_app
from apex.core.config import Settings
from apex.db.session import make_engine, make_session_factory
from apex.news.macro import value_known_before
from apex.news.positioning import fetch_cot, fetch_finra, latest_positioning
from apex.news.sources import FEEDS, Fetcher
from apex.scheduler import Scheduler, parse_jobs
from tests.conftest import utc

FINRA = ("Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"
         "20251208|SPY|4000000|1000|10000000|B,Q,N\n"
         "20251208|AAPL|2500000|0|5000000|B,Q,N\n"
         "20251208|ZZZZ|1|0|2|Q\n"
         "20251208|QQQ|0|0|0|Q\n")
COT = [{"report_date_as_yyyy_mm_dd": "2025-12-02T00:00:00.000", "open_interest_all": "2000000",
        "lev_money_positions_long": "300000", "lev_money_positions_short": "500000",
        "asset_mgr_positions_long": "900000", "asset_mgr_positions_short": "100000"},
       {"report_date_as_yyyy_mm_dd": "2025-12-09T00:00:00.000", "open_interest_all": "2000000",
        "lev_money_positions_long": "300000", "lev_money_positions_short": "700000",
        "asset_mgr_positions_long": "900000", "asset_mgr_positions_short": "100000"}]
GDELT = {"articles": [{"url": "https://www.reuters.com/markets/x", "title": "Stocks slip as yields rise",
                       "seendate": "20251210T190000Z", "domain": "reuters.com",
                       "sourcecountry": "United States"},
                      {"url": "javascript:x", "title": "bad", "seendate": "20251210T190000Z"}]}


def _client():
    calls = []

    def handle(req):
        calls.append(req)
        u = str(req.url)
        if "CNMSshvol20251208" in u:
            return httpx.Response(200, text=FINRA)
        if "CNMSshvol" in u:
            return httpx.Response(403)  # FINRA has no file for that day
        if "publicreporting.cftc.gov" in u:
            code = "13874A" if "13874A" in req.url.params["$where"] else "other"
            return httpx.Response(200, json=COT if code == "13874A" else [])
        if "gdeltproject" in u:
            return httpx.Response(200, json=GDELT)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handle)), calls


def test_finra_short_share_skips_missing_days_and_unknown_symbols(session):
    client, calls = _client()
    r = fetch_finra(session, client, ["SPY", "AAPL", "QQQ"], days=7, today=date(2025, 12, 10),
                    now=utc(2025, 12, 10, 12), sleep=lambda _: None)
    assert r == {"stored": {"SPY": 1, "AAPL": 1, "QQQ": 0}, "errors": []}
    assert not any("CNMSshvol20251206" in str(c.url) for c in calls)  # weekend not asked for
    assert value_known_before(session, "SVR:SPY", utc(2025, 12, 8, 23)) is None
    assert value_known_before(session, "SVR:SPY", utc(2025, 12, 9)) == (date(2025, 12, 8), 0.4)


def test_cot_positions_count_from_friday_publication(session):
    client, _ = _client()
    r = fetch_cot(session, client, now=utc(2025, 12, 15))
    assert r["stored"] == {"COT:13874A:LEV": 2, "COT:13874A:AM": 2}
    assert any("Nasdaq" in e for e in r["errors"])  # empty answer reported, not hidden
    # Tuesday 9 Dec positions are published Friday 12 Dec
    assert value_known_before(session, "COT:13874A:LEV", utc(2025, 12, 12, 12)) == \
        (date(2025, 12, 2), -0.1)
    assert value_known_before(session, "COT:13874A:LEV", utc(2025, 12, 13)) == \
        (date(2025, 12, 9), -0.2)
    pos = latest_positioning(session)
    lev = next(f for f in pos["futures"] if f["who"] == "Hedge funds")
    assert lev["net_share"] == -0.2 and lev["previous"] == -0.1


def test_gdelt_feed_reads_articles_with_capture_times():
    client, calls = _client()
    [e] = Fetcher(client, sleep=lambda _: None).entries(FEEDS["gdelt-markets"])
    assert e.title == "Stocks slip as yields rise" and e.published_at == utc(2025, 12, 10, 19)
    assert calls[0].url.params["format"] == "json"


def test_positioning_job_and_endpoint(db_url):
    client, _ = _client()
    engine = make_engine(db_url)
    settings = Settings(_env_file=None, database_url=db_url)
    jobs = parse_jobs({"jobs": [{"name": "pos", "type": "positioning", "symbols": ["SPY"],
                                 "days": 3, "cot_days": 30, "every_minutes": 1440}]})
    sch = Scheduler(jobs, settings, make_session_factory(engine), http_client=client,
                    http_sleep=lambda _: None)
    [res] = sch.run_cycle(utc(2025, 12, 10, 12))
    assert res["short_volume"] == 1 and res["futures"]["COT:13874A:LEV"] == 2
    c = TestClient(create_app(settings, make_session_factory(engine)))
    pos = c.get("/positioning").json()
    assert pos["short"] == [{"symbol": "SPY", "date": "2025-12-08", "short_share": 0.4,
                             "avg_20d": 0.4}]
    assert 'id="nshort"' in c.get("/ui/").text
    engine.dispose()
