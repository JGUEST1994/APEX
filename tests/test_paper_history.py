"""Practice account value over time, rebuilt from the fill ledger (read-only)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apex.api.app import create_app
from apex.core.config import Settings
from apex.db.session import make_engine, make_session_factory
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.execution import store
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.paper_history import equity_history
from tests.conftest import utc

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("symbols", [["SPY"], ["SPY", "BTCUSDT"]])
def test_last_point_equals_the_account_value(session, symbols):
    for sym in symbols:
        ingest_bars(session, SyntheticProvider(annual_vol=0.4), by_symbol()[sym], Timeframe.D1,
                    utc(2021, 1, 1), utc(2022, 6, 1))
    store.create_account(session, "demo", strategy_key="TF-SMA-001",
                         symbol=symbols if len(symbols) > 1 else symbols[0], timeframe="1d", source="synthetic", params={"fast": 10, "slow": 40})
    session.commit()
    r = store.run_account(session, "demo", now=NOW)
    eq = equity_history(session, "demo")
    assert len(eq) >= 300 and eq.iloc[0] == pytest.approx(100_000.0)
    assert eq.iloc[-1] == pytest.approx(r["equity"], rel=1e-9)
    assert eq.index.is_monotonic_increasing and eq.notna().all()
    with pytest.raises(KeyError):
        equity_history(session, "ghost")


def test_api_and_not_yet_run(db_url, session):
    ingest_bars(session, SyntheticProvider(), by_symbol()["SPY"], Timeframe.D1,
                utc(2021, 1, 1), utc(2021, 6, 1))
    store.create_account(session, "fresh", strategy_key="TF-SMA-001", symbol="SPY",
                         timeframe="1d", source="synthetic", params={"fast": 10, "slow": 40})
    session.commit()
    engine = make_engine(db_url)
    c = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                              make_session_factory(engine)))
    assert c.get("/paper/accounts/fresh/equity").json()["equity"] == []
    store.run_account(session, "fresh", now=NOW)
    j = c.get("/paper/accounts/fresh/equity?points=20").json()
    assert j["simulated"] and 2 <= len(j["equity"]) <= 21 and len(j["ts"]) == len(j["equity"])
    assert c.get("/paper/accounts/ghost/equity").status_code == 404
    assert c.post("/paper/accounts/fresh/equity").status_code == 405
    engine.dispose()
