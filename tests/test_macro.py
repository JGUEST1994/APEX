"""Economic calendar and FRED/ALFRED series: vintages, look-ahead, failures, API. Mocked HTTP."""

from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from apex.api.app import create_app
from apex.core.config import Settings
from apex.core.errors import ProviderError
from apex.db.models import MacroObservation
from apex.db.session import make_engine, make_session_factory
from apex.news.fred import FredClient
from apex.news.macro import (
    event_dates,
    fetch_macro,
    latest_values,
    upcoming,
    value_known_before,
)
from apex.scheduler import Scheduler, parse_jobs
from tests.conftest import utc

RELEASES = {"count": 2, "releases": [{"id": 50, "name": "Employment Situation"},
                                     {"id": 101, "name": "FOMC Press Release"}]}
DATES = {50: ["2025-11-07", "2025-12-05", "2026-01-09"], 101: ["2025-12-10"]}
# UNRATE for October 2025: first published 4.1 on 7 Nov, revised to 4.2 on 5 Dec.
OBS = {"UNRATE": [
    {"realtime_start": "2025-11-07", "realtime_end": "2025-12-04", "date": "2025-10-01", "value": "4.1"},
    {"realtime_start": "2025-12-05", "realtime_end": "9999-12-31", "date": "2025-10-01", "value": "4.2"},
    {"realtime_start": "2025-12-05", "realtime_end": "9999-12-31", "date": "2025-11-01", "value": "4.3"},
    {"realtime_start": "2025-12-05", "realtime_end": "9999-12-31", "date": "2025-12-01", "value": "."}],
    "VIXCLS": [{"realtime_start": "2025-12-11", "realtime_end": "9999-12-31",
                "date": "2025-12-10", "value": "17.5"}]}


def _fred(fail=()):
    calls = []

    def handle(req):
        calls.append(req)
        p, q = req.url.path, dict(req.url.params)
        if p.endswith("/releases"):
            return httpx.Response(200, json=RELEASES)
        if p.endswith("/release/dates"):
            rid = int(q["release_id"])
            return httpx.Response(200, json={"release_dates": [
                {"release_id": rid, "date": d} for d in DATES[rid]]})
        if p.endswith("/series/observations"):
            sid = q["series_id"]
            if sid in fail:
                return httpx.Response(500)
            return httpx.Response(200, json={"observations": OBS.get(sid, [])})
        return httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handle))
    return FredClient("SECRETKEY99", client, sleep=lambda _: None), calls


def test_fetch_stores_calendar_and_every_vintage_once(session):
    fred, calls = _fred()
    r = fetch_macro(session, fred, series=["UNRATE", "VIXCLS"], now=utc(2025, 12, 20))
    assert r["events"] == {"US jobs report": 3, "Fed interest-rate decision": 1}
    assert r["series"] == {"UNRATE": 3, "VIXCLS": 1}  # "." skipped
    assert any("not found" in e for e in r["errors"])  # releases FRED didn't list are named
    again = fetch_macro(session, fred, series=["UNRATE"], now=utc(2025, 12, 21))
    assert again["series"] == {"UNRATE": 0} and set(again["events"].values()) == {0}
    assert all(c.url.params["api_key"] == "SECRETKEY99" for c in calls)
    rows = session.scalars(select(MacroObservation).where(MacroObservation.series_id == "UNRATE")
                           .order_by(MacroObservation.vintage)).all()
    assert [(r.vintage, r.value) for r in rows if r.obs_date == date(2025, 10, 1)] == [
        (date(2025, 11, 7), 4.1), (date(2025, 12, 5), 4.2)]


def test_backtests_see_the_figure_that_was_public_then(session):
    fred, _ = _fred()
    fetch_macro(session, fred, series=["UNRATE"], releases=False, now=utc(2025, 12, 20))
    assert value_known_before(session, "UNRATE", utc(2025, 11, 7, 23)) is None  # same day: not yet
    assert value_known_before(session, "UNRATE", utc(2025, 11, 8)) == (date(2025, 10, 1), 4.1)
    assert value_known_before(session, "UNRATE", utc(2025, 12, 5, 12)) == (date(2025, 10, 1), 4.1)
    assert value_known_before(session, "UNRATE", utc(2025, 12, 6)) == (date(2025, 11, 1), 4.3)
    with pytest.raises(ValueError):
        value_known_before(session, "UNRATE", utc(2025, 12, 6).replace(tzinfo=None))
    [v] = latest_values(session)
    assert v["series_id"] == "UNRATE" and v["value"] == 4.3 and v["previous"] == 4.2


def test_calendar_queries(session):
    fred, _ = _fred()
    fetch_macro(session, fred, series=[], now=utc(2025, 12, 1))
    assert [e.name for e in upcoming(session, date(2025, 12, 1), 14)] == [
        "US jobs report", "Fed interest-rate decision"]
    assert event_dates(session, ["fed"]) == {date(2025, 12, 10)}
    assert len(event_dates(session)) == 4


def test_failures_are_isolated_and_never_show_the_key(session):
    fred, _ = _fred(fail=("VIXCLS",))
    r = fetch_macro(session, fred, series=["VIXCLS", "UNRATE"], releases=False,
                    now=utc(2025, 12, 20))
    assert r["series"] == {"UNRATE": 3}
    assert r["errors"] and "SECRETKEY99" not in " ".join(r["errors"])
    with pytest.raises(ProviderError, match="APEX_FRED_API_KEY"):
        FredClient("")


def test_scheduler_macro_job_and_endpoints(db_url):
    fred, _ = _fred()
    engine = make_engine(db_url)
    settings = Settings(_env_file=None, database_url=db_url)
    jobs = parse_jobs({"jobs": [{"name": "macro", "type": "macro", "series": ["UNRATE", "VIXCLS"],
                                 "every_minutes": 1440}]})
    sch = Scheduler(jobs, settings, make_session_factory(engine), fred_client=fred)
    [res] = sch.run_cycle(utc(2025, 12, 20))
    assert res["series"] == {"UNRATE": 3, "VIXCLS": 1}
    c = TestClient(create_app(settings, make_session_factory(engine)))
    cal = c.get("/calendar?start=2025-12-01&days=14").json()
    assert [e["name"] for e in cal] == ["US jobs report", "Fed interest-rate decision"]
    assert c.get("/calendar?start=nope").status_code == 422
    mac = {m["series_id"]: m for m in c.get("/macro").json()}
    assert mac["VIXCLS"]["value"] == 17.5 and mac["VIXCLS"]["source_url"].endswith("/VIXCLS")
    assert 'id="ncal"' in c.get("/ui/").text
    engine.dispose()
