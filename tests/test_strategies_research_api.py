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


_DATASETS = {
    "spy_daily": ("SPY", Timeframe.D1, utc(2022, 1, 1), utc(2025, 1, 1), 400),
    "es_15m": ("ES", Timeframe.M15, utc(2025, 3, 3), utc(2025, 3, 21), 700),
    "spy_5m": ("SPY", Timeframe.M5, utc(2025, 3, 3), utc(2025, 3, 14), 500),
}


@pytest.mark.parametrize("data", list(_DATASETS))
@pytest.mark.parametrize("key", list(all_strategies()))
def test_strategies_have_no_lookahead(key, data):
    sym, tf, start, end, cut = _DATASETS[data]
    strat = all_strategies()[key]()
    bars = SyntheticProvider(annual_vol=0.6).fetch_bars(by_symbol()[sym], tf, start, end)
    params = strat.params()
    full = strat.target_position(bars, params)
    partial = strat.target_position(bars.iloc[:cut].copy(), params)
    assert len(full) == len(bars)
    assert full.iloc[:cut].equals(partial), "signal changed when future bars were added"
    assert full.between(-1, 1).all() and not full.isna().any()


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


def test_hypothesis_bank():
    from pathlib import Path

    from apex.strategies.hypotheses import CATEGORIES, HYPOTHESES, to_markdown

    ids = [h.id for h in HYPOTHESES]
    assert len(ids) >= 50 and len(set(ids)) == len(ids)
    assert {h.category for h in HYPOTHESES} == set(CATEGORIES)
    registry = all_strategies()
    implemented = {h.implemented for h in HYPOTHESES if h.implemented}
    assert implemented <= set(registry)
    assert set(registry) <= implemented, "every registered strategy needs a hypothesis entry"
    for h in HYPOTHESES:
        assert h.data in {"ohlcv", "ticks", "l2", "multi"}
        assert all([h.entry, h.exit, h.stop, h.markets, h.timeframes, h.idea])
    doc = Path(__file__).resolve().parent.parent / "docs" / "STRATEGY_HYPOTHESES.md"
    assert doc.read_text().strip() == to_markdown().strip(), "run: apex hypotheses > docs/STRATEGY_HYPOTHESES.md"


@pytest.mark.parametrize("key", list(all_strategies()))
def test_every_strategy_trades_on_some_data(key):
    from apex.backtest.engine import run_backtest

    trades = 0
    for sym, tf, start, end, _ in _DATASETS.values():
        bars = SyntheticProvider(annual_vol=0.6).fetch_bars(by_symbol()[sym], tf, start, end)
        trades += run_backtest(bars, all_strategies()[key](), by_symbol()[sym]).metrics["trades"]
    assert trades > 0
