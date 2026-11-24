"""Trade review page data: every practice trade grouped, plus account health (read-only)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apex.api.app import create_app
from apex.core.config import Settings
from apex.db.models import PaperAccount
from apex.db.session import make_engine, make_session_factory
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.execution import store
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.paper_trades import trades
from apex.trade_review import review
from tests.conftest import utc

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def two(session):
    for sym in ("SPY", "BTCUSDT"):
        ingest_bars(session, SyntheticProvider(annual_vol=0.4), by_symbol()[sym], Timeframe.D1,
                    utc(2021, 1, 1), utc(2022, 6, 1))
    for name, sym in (("a", "SPY"), ("b", "BTCUSDT")):
        store.create_account(session, name, strategy_key="TF-SMA-001", symbol=sym,
                             timeframe="1d", source="synthetic",
                             params={"fast": 5, "slow": 20})
        session.commit()
        store.run_account(session, name, now=NOW)
    session.commit()
    return session


def test_review_groups_add_up(two):
    r = review(two)
    n = len(trades(two, "a", 10**6)["closed"]) + len(trades(two, "b", 10**6)["closed"])
    s = r["summary"]
    assert s["trades"] == n == r["trades_total"] and n > 5
    for g in ("by_market", "by_strategy", "by_asset_class", "by_exit_reason", "by_side",
              "by_hold", "by_weekday", "by_month"):
        assert sum(x["trades"] for x in r[g]) == n, g
        assert sum(x["pnl"] for x in r[g]) == pytest.approx(s["total_pnl"]), g
    assert {x["key"] for x in r["by_market"]} == {"SPY", "BTCUSDT"}
    assert {x["key"] for x in r["by_asset_class"]} == {"Stocks and funds", "Crypto"}
    assert [x["key"] for x in r["by_side"]] == sorted({x["key"] for x in r["by_side"]})
    assert r["curve"][-1]["cumulative"] == pytest.approx(s["total_pnl"])
    assert all(x["pnl"] > 0 for x in r["top_wins"]) and all(x["pnl"] < 0 for x in r["top_losses"])
    assert s["profit_per_trade"] == pytest.approx(s["total_pnl"] / n)
    assert [h["account"] for h in r["health"]] == ["a", "b"]


def test_filters_and_health(two):
    one = review(two, "a")
    assert {t["account"] for t in one["trades"]} == {"a"} and len(one["health"]) == 1
    mid = utc(2021, 9, 1)
    later = review(two, start=mid)
    assert all(t["closed"] >= mid.isoformat() for t in later["trades"])
    assert later["summary"]["trades"] + review(two, end=mid)["summary"]["trades"] == \
        review(two)["summary"]["trades"]
    h = one["health"][0]
    acc = two.query(PaperAccount).filter_by(name="a").one()
    assert h["drop_from_peak"] >= 0 and h["peak"] >= h["value"]
    assert {g["label"] for g in h["limits"]} >= {"Losing trades in a row", "Drop from best value"}
    streak = next(g for g in h["limits"] if g["label"] == "Losing trades in a row")
    assert streak["now"] == acc.consecutive_losses and streak["limit"] == 6
    assert h["value"] == pytest.approx(h["cash"] + sum(
        t["units"] * t["last_price"] * (1 if t["side"] == "long" else -1)
        for t in trades(two, "a")["open"]))
    with pytest.raises(KeyError):
        review(two, "ghost")


def test_api(db_url, two):
    engine = make_engine(db_url)
    c = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                              make_session_factory(engine)))
    j = c.get("/review").json()
    assert j["simulated"] and j["accounts"] == ["a", "b"] and j["summary"]["trades"] > 0
    assert c.get("/review?account=a&start=2021-06-01T00:00:00Z").status_code == 200
    assert c.get("/review?start=2021-06-01T00:00:00").status_code == 422
    assert c.get("/review?account=ghost").status_code == 404
    assert c.post("/review").status_code == 405
    engine.dispose()
