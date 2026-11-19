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
