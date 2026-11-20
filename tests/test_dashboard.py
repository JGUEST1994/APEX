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
    from apex.strategies.multi import all_multi_strategies
    assert {s["key"] for s in st} == set(all_strategies()) | set(all_multi_strategies())
    assert {s["key"]: s["instruments"] for s in st}["STAT-PAIR-001"] == 2
    assert all(s["status"] == "experimental" for s in st)
    by = {s["key"]: s for s in st}
    assert by["BO-GAP-001"]["ideas"] == ["H20"] and by["STAT-PAIR-001"]["ideas"]
    assert all(s["entry_rules"] and s["exit_rules"] and s["asset_classes"] for s in st)
    assert by["TF-SMA-001"]["default_params"]["fast"] > 0
    assert 'id="srules"' in page.text and 'id="scmd"' in page.text
    hs = c.get("/hypotheses").json()
    from apex.strategies.hypotheses import HYPOTHESES
    assert [h["id"] for h in hs] == [h.id for h in HYPOTHESES]
    keys = {s["key"] for s in st}
    assert all(h["implemented"] in keys for h in hs if h["implemented"])
    assert 'id="ideas"' in page.text and '"/hypotheses"' in page.text
    ck = c.get("/system/checks").json()
    lv = {x["name"]: x["level"] for x in ck}
    assert lv["Database tables"] == "OK" and lv["Stored prices"] == "OK"
    assert all(set(x) == {"level", "name", "detail"} for x in ck)
    assert 'id="checks"' in page.text and '"/system/checks"' in page.text
    engine.dispose()


def test_backtest_detail_glossary_only_names_recorded_figures():
    import re

    from apex.backtest.engine import run_backtest

    html = (STATIC / "index.html").read_text()
    block = html[html.index("const GLOSSARY"):html.index("const fmt")]
    keys = set(re.findall(r"^\s+([a-z_]+): \[", block, re.M))
    bars = SyntheticProvider().fetch_bars(by_symbol()["SPY"], Timeframe.D1,
                                          utc(2022, 1, 1), utc(2024, 1, 1))
    recorded = set(run_backtest(bars, all_strategies()["TF-SMA-001"](),
                                by_symbol()["SPY"]).metrics)
    validation = (STATIC.parent.parent / "backtest" / "validation.py").read_text()
    recorded |= set(re.findall(r'"([a-z_]+)":', validation))
    assert len(keys) >= 20 and keys <= recorded, keys - recorded
    assert 'id="xmetrics"' in html and '"wrap"' in html


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
