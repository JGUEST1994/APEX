import pytest
from fastapi.testclient import TestClient

from apex.ai.provider import AIUnavailable, get_provider
from apex.api.app import create_app
from apex.core.config import Settings
from apex.db.session import make_engine, make_session_factory
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.research.library import add_document, keyword_search
from apex.strategies.base import StrategyStatus as S
from apex.strategies.base import can_transition
from apex.strategies.registry import all_strategies
from tests.conftest import utc


def test_lifecycle_rules():
    assert can_transition(S.EXPERIMENTAL, S.BACKTESTING)
    assert not can_transition(S.EXPERIMENTAL, S.APPROVED)
    assert not can_transition(S.BACKTESTING, S.PAPER_TRADING)
    assert can_transition(S.APPROVED, S.SUSPENDED)
    assert not can_transition(S.SUSPENDED, S.APPROVED)
    assert not can_transition(S.REJECTED, S.EXPERIMENTAL)


@pytest.mark.parametrize("key", list(all_strategies()))
def test_strategies_have_no_lookahead(key):
    strat = all_strategies()[key]()
    bars = SyntheticProvider().fetch_bars(by_symbol()["SPY"], Timeframe.D1,
                                          utc(2022, 1, 1), utc(2025, 1, 1))
    params = strat.params()
    full = strat.target_position(bars, params)
    cut = 400
    partial = strat.target_position(bars.iloc[:cut].copy(), params)
    assert full.iloc[:cut].equals(partial)
    assert full.between(-1, 1).all()


def test_unknown_param_rejected():
    strat = all_strategies()["TF-SMA-001"]()
    with pytest.raises(ValueError):
        strat.params({"nope": 1})


def test_research_library(session):
    d1 = add_document(session, title="VWAP mean reversion notes", source_type="doc",
                      text="Price tends to revert toward VWAP intraday. " * 50,
                      licence_note="own notes")
    add_document(session, title="Breakouts", source_type="doc",
                 text="Opening range breakout with volume confirmation.")
    again = add_document(session, title="dup", source_type="doc",
                         text="Price tends to revert toward VWAP intraday. " * 50)
    session.commit()
    assert again.id == d1.id
    hits = keyword_search(session, "VWAP mean-reversion")
    assert hits and hits[0].document_id == d1.id
    assert keyword_search(session, "the and") == []


def test_ai_is_optional():
    llm = get_provider(Settings(_env_file=None))
    assert not llm.available()
    with pytest.raises(AIUnavailable):
        llm.complete("hi")


def test_api(db_url, session):
    ingest_bars(session, SyntheticProvider(), by_symbol()["BTCUSDT"], Timeframe.D1,
                utc(2025, 1, 1), utc(2025, 2, 1))
    session.commit()
    settings = Settings(_env_file=None, database_url=db_url)
    engine = make_engine(db_url)
    client = TestClient(create_app(settings, make_session_factory(engine)))
    h = client.get("/health").json()
    assert h["database"] and h["live_trading_available"] is False
    assert client.get("/instruments").json()[0]["symbol"] == "BTCUSDT"
    bars = client.get("/instruments/BTCUSDT/bars",
                      params={"timeframe": "1d", "source": "synthetic"}).json()
    assert len(bars) == 31
    assert client.get("/instruments/NOPE/bars",
                      params={"timeframe": "1d", "source": "synthetic"}).status_code == 404
    assert client.get("/ingestion-runs").json()[0]["status"] == "ok"
    engine.dispose()
