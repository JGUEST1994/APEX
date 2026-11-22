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
    assert {"oos_buy_hold_return", "oos_buy_hold_max_drawdown"} <= keys
    assert 'id="xmetrics"' in html and '"wrap"' in html
    assert '["Just holding", (r) => pct(r.buy_hold_return)' in html  # Tournament tab column


def test_dashboard_never_injects_html():
    html = (STATIC / "index.html").read_text()
    assert "innerHTML" not in html.replace("never innerHTML", "")
    # No scripts from the internet: the only script file is the chart library stored in vendor/.
    import re
    srcs = re.findall(r'<script src="([^"]+)"', html)
    assert srcs == ["vendor/lightweight-charts.standalone.production.js"]
    assert all((STATIC / src).is_file() and "//" not in src for src in srcs)
    assert (STATIC / "vendor" / "lightweight-charts.LICENSE").is_file()
    assert "tradingview.com" in html and "attributionLogo: true" in html  # licence credit


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
    rec = c.get("/paper/accounts/demo/record").json()  # created today: no forward days yet
    assert rec["ready"] is False and rec["min_days"] == 30 and rec["reasons"]
    assert rec["limits_changed"] == {}  # standard safety limits: nothing to list
    assert c.get("/paper/accounts/nope/record").status_code == 404
    # nothing on the API can change an account or reset the kill switch
    for path in ("/paper/accounts", "/paper/accounts/demo", "/paper/accounts/demo/consistency",
                 "/paper/accounts/demo/record"):
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


def test_ingestion_runs_show_market_and_findings(db_url, session):
    from apex.db.models import DataQualityIssue

    r = ingest_bars(session, SyntheticProvider(), by_symbol()["QQQ"], Timeframe.D1,
                    utc(2024, 1, 1), utc(2024, 2, 1))
    session.add_all([DataQualityIssue(run_id=r.run_id, kind="gap", severity="warning",
                                      ts=utc(2024, 1, 15), detail="1 bar missing"),
                     DataQualityIssue(run_id=r.run_id, kind="spike", severity="warning",
                                      ts=utc(2024, 1, 20), detail="12 sigma")])
    session.commit()
    c = TestClient(create_app(Settings(_env_file=None, database_url=db_url)))
    run = c.get("/ingestion-runs").json()[0]
    assert run["symbol"] == "QQQ" and run["issues"] >= 2 and run["error"] is None
    iss = c.get(f"/ingestion-runs/{run['id']}/issues").json()
    assert [i["kind"] for i in iss][-2:] == ["gap", "spike"]
    assert iss[-1]["ts"].startswith("2024-01-20") and iss[-1]["detail"] == "12 sigma"
    assert c.get("/ingestion-runs/999999/issues").status_code == 404
    html = (STATIC / "index.html").read_text()
    assert '`/ingestion-runs/' in html and "ISSUE_TEXT" in html and 'id="issues"' in html


def test_backtest_equity_curve_is_replayed_exactly_and_read_only(db_url, session):
    from sqlalchemy import func, select

    from apex.backtest.experiments import run_experiment
    from apex.db.models import Experiment

    ingest_bars(session, SyntheticProvider(), by_symbol()["SPY"], Timeframe.D1,
                utc(2023, 1, 1), utc(2024, 6, 1))
    exp, res = run_experiment(session, all_strategies()["TF-SMA-001"](), symbol="SPY",
                              timeframe=Timeframe.D1, source="synthetic",
                              start=utc(2023, 1, 1), end=utc(2024, 6, 1))
    session.commit()
    engine = make_engine(db_url)
    c = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                              make_session_factory(engine)))
    r = c.get(f"/experiments/{exp.id}/equity?points=50").json()
    assert r["available"] and r["matches_record"] and r["simulated"]
    one = c.get(f"/experiments/{exp.id}").json()
    assert one["id"] == exp.id and one["strategy"] == "TF-SMA-001" and one["simulated"]
    assert one == c.get("/experiments").json()[0]
    page_js = (STATIC / "index.html").read_text()
    assert "r.experiment_id" in page_js and '"/experiments/" + id' in page_js
    assert len(r["equity"]) <= 51 and r["ts"][-1] == res.equity.index[-1].isoformat()
    assert r["equity"][-1] == round(float(res.equity.iloc[-1]), 2)
    assert session.scalar(select(func.count()).select_from(Experiment)) == 1  # nothing recorded
    assert c.get("/experiments/999/equity").status_code == 404
    assert c.get("/experiments/999").status_code == 404
    # Changed prices: refuse rather than draw a different curve.
    from apex.db.models import PriceBar

    bar = session.scalars(select(PriceBar).order_by(PriceBar.ts.desc())).first()
    bar.close = bar.close * 1.5
    session.commit()
    r = c.get(f"/experiments/{exp.id}/equity").json()
    assert not r["available"] and "prices have changed" in r["reason"]
    page = (STATIC / "index.html").read_text()
    assert "/equity`" in page and 'id="xchart"' in page
    engine.dispose()


def test_link_buttons_keep_a_keyboard_focus_ring():
    css = (STATIC / "apex.css").read_text()
    assert css.index(".linkbtn:focus-visible") > css.index(".linkbtn { all: unset")


def test_strategy_history_is_read_only_and_shows_who_moved_it(db_url, session):
    from apex.backtest.experiments import ensure_strategy_version
    from apex.strategies.base import StrategyStatus
    from apex.strategies.lifecycle import transition

    ver = ensure_strategy_version(session, all_strategies()["TF-SMA-001"]())
    transition(session, ver, StrategyStatus.BACKTESTING, reason="first backtest")
    transition(session, ver, StrategyStatus.VALIDATION, reason="passed every tournament gate")
    session.commit()
    engine = make_engine(db_url)
    c = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                              make_session_factory(engine)))
    h = c.get("/strategies/TF-SMA-001/history").json()
    assert [(x["from"], x["to"], x["by"]) for x in h] == [
        (None, "experimental", "system"), ("experimental", "backtesting", "system"),
        ("backtesting", "validation", "system")]
    assert h[-1]["reason"] == "passed every tournament gate" and h[0]["at"].endswith("+00:00")
    assert c.get("/strategies/BO-DONCH-001/history").json() == []
    assert c.get("/strategies/NOPE/history").status_code == 404
    assert c.post("/strategies/TF-SMA-001/history").status_code == 405
    page = c.get("/ui/").text
    assert 'id="shist"' in page and "/history" in page
    engine.dispose()
