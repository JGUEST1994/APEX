"""Phase 10 read-only dashboard: static page, new endpoints, and safe rendering."""

from pathlib import Path

from fastapi.testclient import TestClient

from apex.api.app import create_app
from apex.core.config import Settings
from apex.db.session import make_engine, make_session_factory
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.strategies.registry import all_strategies
from tests.conftest import utc

STATIC = Path(__file__).resolve().parent.parent / "src" / "apex" / "api" / "static"


def test_dashboard_and_endpoints(db_url, session):
    ingest_bars(session, SyntheticProvider(), by_symbol()["SPY"], Timeframe.D1,
                utc(2024, 1, 1), utc(2024, 3, 1))
    session.commit()
    engine = make_engine(db_url)
    c = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                              make_session_factory(engine)))
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/ui/"
    page = c.get("/ui/")
    assert page.status_code == 200 and "SIMULATED RESULTS ONLY" in page.text
    ds = c.get("/datasets").json()
    assert ds == [{"symbol": "SPY", "timeframe": "1d", "source": "synthetic", "bars": ds[0]["bars"],
                   "first": ds[0]["first"], "last": ds[0]["last"]}] and ds[0]["bars"] > 30
    st = c.get("/strategies").json()
    assert {s["key"] for s in st} == set(all_strategies())
    assert all(s["status"] == "experimental" for s in st)
    engine.dispose()


def test_dashboard_never_injects_html():
    html = (STATIC / "index.html").read_text()
    assert "innerHTML" not in html.replace("never innerHTML", "")
    assert "<script src=" not in html  # no third-party scripts


def test_paper_endpoints_are_read_only(db_url, session):
    from datetime import UTC, datetime

    from apex.execution import store

    ingest_bars(session, SyntheticProvider(annual_vol=0.4), by_symbol()["SPY"], Timeframe.D1,
                utc(2021, 1, 1), utc(2023, 1, 1))
    store.create_account(session, "demo", strategy_key="TF-SMA-001", symbol="SPY",
                         timeframe="1d", source="synthetic", params={"fast": 10, "slow": 40})
    session.commit()
    store.run_account(session, "demo", now=datetime(2026, 1, 1, tzinfo=UTC))
    engine = make_engine(db_url)
    c = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                              make_session_factory(engine)))
    accts = c.get("/paper/accounts").json()
    assert [a["name"] for a in accts] == ["demo"] and accts[0]["simulated"] is True
    assert accts[0]["kill_switch"]["engaged"] is False
    one = c.get("/paper/accounts/demo").json()
    assert one["fills"] and one["orders"] and one["fills"][0]["ts"] >= one["fills"][-1]["ts"]
    assert c.get("/paper/accounts/nope").status_code == 404
    cmp = c.get("/paper/accounts/demo/consistency").json()
    assert cmp["consistent"] is True and cmp["direction_agreement"] == 1.0
    assert c.get("/paper/accounts/nope/consistency").status_code == 404
    # nothing on the API can change an account or reset the kill switch
    for path in ("/paper/accounts", "/paper/accounts/demo", "/paper/accounts/demo/consistency"):
        assert c.post(path).status_code == 405 and c.delete(path).status_code == 405
    assert not [r for r in c.app.routes if "reset" in getattr(r, "path", "")]


def test_dashboard_brand_assets_and_banner(db_url):
    engine = make_engine(db_url)
    c = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                              make_session_factory(engine)))
    page = c.get("/ui/").text
    # the safety banner stays the first thing in the body
    assert page.index('id="banner"') < page.index('class="frame"')
    for path in ("apex.css", "brand/favicon.svg", "brand/favicon.ico", "brand/apple-touch-icon.png"):
        assert path in page
        assert c.get("/ui/" + path).status_code == 200, path
    engine.dispose()


def test_dashboard_has_empty_and_error_states():
    html = (STATIC / "index.html").read_text()
    # every table gets a next-step message when empty, and a failed load explains itself
    for table_id in ("runs", "lb", "exps", "paccts", "hits"):
        assert f'id="{table_id}"' in html and f"  {table_id}: " in html
    assert "This page couldn't load." in html and 'id="glance"' in html
