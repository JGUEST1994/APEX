"""Practice trades as round trips with win/loss (read-only report from the fill ledger)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apex.api.app import create_app
from apex.core.config import Settings
from apex.db.models import PaperAccount, PaperFill
from apex.db.session import make_engine, make_session_factory
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.execution import store
from apex.execution.paper import Fill, Portfolio
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.marketdata.repository import get_instrument, to_spec
from apex.paper_trades import trades
from tests.conftest import utc

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _account(session, name="hand"):
    ingest_bars(session, SyntheticProvider(), by_symbol()["SPY"], Timeframe.D1,
                utc(2021, 1, 1), utc(2021, 3, 1))
    store.create_account(session, name, strategy_key="TF-SMA-001", symbol="SPY",
                         timeframe="1d", source="synthetic", params={"fast": 10, "slow": 40})
    session.commit()
    return session.query(PaperAccount).filter_by(name=name).one()


def _fill(session, acc, day, qty, price, cost=1.0):
    session.add(PaperFill(account_id=acc.id, client_order_id=f"c{day}{qty}", symbol="SPY",
                          qty=qty, price=price, cost=cost, ts=utc(2021, 1, day)))


def test_round_trips_by_hand(session):
    acc = _account(session)
    _fill(session, acc, 4, 10, 100)    # buy 10 @100
    _fill(session, acc, 5, 10, 110)    # top up: avg 105
    _fill(session, acc, 6, -20, 120)   # sell all: +300 - 3 costs -> win
    _fill(session, acc, 7, -5, 120)    # short 5 @120
    _fill(session, acc, 8, 10, 125)    # reverse: short loses 25 + 2 costs; long 5 @125 opens
    _fill(session, acc, 11, -5, 125)   # flat at entry: only its cost -> loss of 1
    _fill(session, acc, 12, 3, 100)    # still open
    acc.last_bar_ts = utc(2021, 1, 14)
    session.commit()
    r = trades(session, "hand")
    c = r["closed"]
    assert [t["result"] for t in c] == ["loss", "loss", "win"]  # newest first
    assert c[2]["pnl"] == pytest.approx(297.0) and c[2]["side"] == "long"
    assert c[2]["entry_price"] == pytest.approx(105) and c[2]["exit_price"] == pytest.approx(120)
    assert c[2]["units"] == 20 and c[2]["days_held"] == pytest.approx(2)
    assert c[1]["side"] == "short" and c[1]["pnl"] == pytest.approx(-27.0)
    assert c[0]["side"] == "long" and c[0]["pnl"] == pytest.approx(-1.0)
    assert c[0]["opened"] == utc(2021, 1, 8).isoformat()
    s = r["summary"]
    assert s["trades"] == 3 and s["wins"] == 1 and s["losses"] == 2
    assert s["win_rate"] == pytest.approx(1 / 3) and s["total_pnl"] == pytest.approx(269.0)
    assert s["average_win"] == pytest.approx(297) and s["average_loss"] == pytest.approx(-14)
    assert s["profit_factor"] == pytest.approx(297 / 28)
    assert s["streak"] == {"kind": "loss", "count": 2}
    (o,) = r["open"]
    assert o["units"] == 3 and o["entry_price"] == 100 and o["last_price"] is not None
    assert o["unrealised"] == pytest.approx(3 * (o["last_price"] - 100))
    assert o["pnl"] == pytest.approx(o["unrealised"] - 1.0)
    assert r["closed_total"] == 3 and len(trades(session, "hand", limit=1)["closed"]) == 1
    with pytest.raises(KeyError):
        trades(session, "ghost")


def test_matches_the_paper_portfolio(session):
    """Same P&L and losing streak as the paper portfolio that drives the kill switch."""
    ingest_bars(session, SyntheticProvider(annual_vol=0.4), by_symbol()["SPY"], Timeframe.D1,
                utc(2021, 1, 1), utc(2022, 6, 1))
    store.create_account(session, "demo", strategy_key="TF-SMA-001", symbol="SPY",
                         timeframe="1d", source="synthetic", params={"fast": 5, "slow": 20})
    session.commit()
    store.run_account(session, "demo", now=NOW)
    acc = session.query(PaperAccount).filter_by(name="demo").one()
    r = trades(session, "demo", limit=1000)
    assert r["summary"]["trades"] >= 3
    pf, spec = Portfolio(cash=0.0), to_spec(get_instrument(session, "SPY"))
    for f in session.query(PaperFill).filter_by(account_id=acc.id).order_by(PaperFill.id):
        pf.apply(Fill(f.client_order_id, f.symbol, f.qty, f.price, f.cost), spec)
    losses = 0
    for t in r["closed"]:
        if t["pnl"] >= 0:
            break
        losses += 1
    assert losses == pf.consecutive_losses == acc.consecutive_losses
    open_part = sum(pf.trade_pnl.values())
    total = r["summary"]["total_pnl"] + open_part
    all_costs = sum(f.cost for f in session.query(PaperFill).filter_by(account_id=acc.id))
    assert total == pytest.approx(pf.realised - all_costs, abs=1e-6)


def test_api(db_url, session):
    acc = _account(session, "api")
    _fill(session, acc, 4, 1, 100)
    _fill(session, acc, 5, -1, 90)
    acc.last_bar_ts = utc(2021, 1, 6)
    session.commit()
    engine = make_engine(db_url)
    c = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                              make_session_factory(engine)))
    j = c.get("/paper/accounts/api/trades").json()
    assert j["simulated"] and j["summary"]["losses"] == 1 and j["open"] == []
    assert j["closed"][0]["pnl"] == pytest.approx(-12.0)
    assert c.get("/paper/accounts/ghost/trades").status_code == 404
    assert c.post("/paper/accounts/api/trades").status_code == 405
    assert c.get("/paper/accounts/api/trades?limit=0").status_code == 422
    engine.dispose()


def test_exit_reasons(session):
    acc = _account(session, "why")
    _fill(session, acc, 4, 10, 100)
    session.add(PaperFill(account_id=acc.id, client_order_id="o1-x", symbol="SPY", qty=-10,
                          price=101, cost=1.0, ts=utc(2021, 1, 5)))  # flip: exit then short
    _fill(session, acc, 5, -10, 101)
    session.add(PaperFill(account_id=acc.id, client_order_id="o2-x", symbol="SPY", qty=10,
                          price=99, cost=1.0, ts=utc(2021, 1, 6)))   # strategy exit
    _fill(session, acc, 7, 5, 100)
    _fill(session, acc, 8, -5, 100)                                   # resized to nothing
    _fill(session, acc, 11, 5, 100)
    session.add(PaperFill(account_id=acc.id, client_order_id="o3-x", symbol="SPY", qty=-5,
                          price=98, cost=1.0, ts=utc(2021, 1, 13)))  # after the kill switch
    acc.kill_engaged, acc.kill_at, acc.kill_reason = True, utc(2021, 1, 12), "test"
    acc.last_bar_ts = utc(2021, 1, 13)
    session.commit()
    got = [t["exit_reason"] for t in trades(session, "why")["closed"]]
    assert got == ["safety", "resize", "signal", "flip"]
