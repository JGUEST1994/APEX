"""Paper trading for multi-instrument strategies (pairs, baskets, cross-asset)."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from apex.core.config import TradingMode
from apex.db.models import PaperAccount, PaperFill
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.execution import store
from apex.execution.consistency import compare
from apex.execution.trader import PaperTrader
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.risk.limits import RiskLimits
from apex.risk.service import RiskService
from apex.strategies.multi import align, all_multi_strategies
from tests.conftest import utc

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SYMS = by_symbol()


def _bars(close):
    close = np.asarray(close, float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"ts": pd.date_range("2021-01-01", periods=len(close), freq="1D",
                                              tz="UTC"),
                         "open": open_, "high": np.maximum(open_, close) * 1.002,
                         "low": np.minimum(open_, close) * 0.998, "close": close,
                         "volume": 1.0})


def _pair(n=400, seed=3):
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.normal(0, 0.01, n))
    spread = np.zeros(n)
    for i in range(1, n):  # mean-reverting spread
        spread[i] = 0.9 * spread[i - 1] + rng.normal(0, 0.01)
    return {"QQQ": _bars(300 * np.exp(x + spread)), "SPY": _bars(100 * np.exp(x))}


def _run(trader, frames):
    rows = {s: f.to_dict("records") for s, f in frames.items()}
    for i in range(len(next(iter(rows.values())))):
        trader.on_bars({s: r[i] for s, r in rows.items()})


def _trader(key="STAT-PAIR-001", syms=("QQQ", "SPY"), **kw):
    strat = all_multi_strategies()[key]()
    risk = RiskService(RiskLimits(), TradingMode.PAPER)
    return PaperTrader(strat, [SYMS[s] for s in syms], risk, history=1000, **kw)


def test_pairs_paper_positions_follow_the_strategy_weights():
    frames = _pair()
    t = _trader()
    held = {s: [] for s in frames}
    rows = {s: f.to_dict("records") for s, f in frames.items()}
    for i in range(len(rows["SPY"])):
        t.on_bars({s: r[i] for s, r in rows.items()})
        for s in frames:
            held[s].append(np.sign(t.pf.units.get(s, 0.0)))
    w = all_multi_strategies()["STAT-PAIR-001"]().target_weights(
        align(frames), all_multi_strategies()["STAT-PAIR-001"]().params(None))
    for s in frames:
        want = np.sign(w[s].fillna(0).to_numpy())[:-1]  # decided at t, filled at t+1 open
        got = np.array(held[s])[1:]
        assert (want != 0).sum() > 20  # the pair actually traded
        assert (want == got).mean() > 0.95
    assert not [e for e in t.events if e.kind == "refused"]
    longs = [np.sign(t.pf.units.get(s, 0.0)) for s in frames]
    assert longs[0] == 0 or longs[0] == -longs[1]  # one leg long, the other short


def test_multi_legs_are_capped_by_the_risk_limits():
    frames = _pair()
    t = _trader()
    rows = {s: f.to_dict("records") for s, f in frames.items()}
    lim = t.risk.limits
    for i in range(len(rows["SPY"])):
        t.on_bars({s: r[i] for s, r in rows.items()})
        px = t.last_prices()
        eq = t.pf.equity(px, t.specs)
        for s, u in t.pf.units.items():  # one bar of drift allowed on top of the cap
            assert abs(u) * px[s] <= lim.max_position_notional * eq * 1.1


def test_kill_switch_flattens_every_leg():
    frames = _pair()
    t = _trader()
    rows = {s: f.to_dict("records") for s, f in frames.items()}
    i = 0
    while not any(t.pf.units.values()):
        t.on_bars({s: r[i] for s, r in rows.items()})
        i += 1
    t.risk.kill.engage("test", NOW, "operator")
    for j in (i, i + 1):
        t.on_bars({s: r[j] for s, r in rows.items()})
    assert not any(t.pf.units.values())


def test_wrong_number_of_legs_is_refused():
    with pytest.raises(ValueError, match="needs 2"):
        _trader(syms=("SPY", "QQQ", "AAPL"))
    with pytest.raises(ValueError, match="at least 2 holdings"):
        _trader("STAT-BASKET-001", syms=("SPY", "QQQ"))


def test_a_closed_leg_means_no_trading_that_bar():
    frames = _pair(200)
    t = _trader()
    _run(t, frames)
    before = dict(t.pf.units), len(t.broker.working)
    nxt = frames["SPY"].iloc[-1].to_dict()
    nxt["ts"] = nxt["ts"] + pd.Timedelta(days=1)
    t.on_bars({"SPY": nxt})  # QQQ has no bar at this timestamp
    assert (dict(t.pf.units), len(t.broker.working)) == before


@pytest.fixture
def seeded(session):
    p = SyntheticProvider(annual_vol=0.4)
    for sym in ("SPY", "QQQ", "AAPL"):
        ingest_bars(session, p, SYMS[sym], Timeframe.D1, utc(2021, 1, 1), utc(2023, 1, 1))
    session.commit()
    return session


def test_multi_strategy_account_persists_and_restarts_identically(seeded):
    s = seeded
    for name in ("straight", "restarted"):
        store.create_account(s, name, strategy_key="TF-XASSET-001",
                             symbol=["SPY", "QQQ", "AAPL"], timeframe="1d",
                             source="synthetic", params={"lookback": 60})
    s.commit()
    store.run_account(s, "straight", now=NOW)
    store.run_account(s, "restarted", now=NOW, max_bars=250)
    store.run_account(s, "restarted", now=NOW)

    def ledger(name):
        acc = s.scalar(select(PaperAccount).where(PaperAccount.name == name))
        return acc, [(f.ts, f.symbol, f.qty, round(f.price, 8)) for f in s.scalars(
            select(PaperFill).where(PaperFill.account_id == acc.id).order_by(PaperFill.id))]

    a, fa = ledger("straight")
    _, fb = ledger("restarted")
    assert fa and fa == fb
    assert store.reconcile(s, a) == []
    rep = compare(s, "straight")
    assert rep["consistent"] is True, rep["notes"]
    assert set(rep["per_symbol"]) == {"SPY", "QQQ", "AAPL"}
    assert rep["direction_agreement"] >= 0.95 and rep["max_fill_price_gap_bps"] <= 1.0
    assert "backtested together" in " ".join(rep["notes"])


def test_pairs_report_agrees_and_spots_a_wrong_fill(seeded):
    s = seeded
    store.create_account(s, "pair", strategy_key="STAT-PAIR-001", symbol=["QQQ", "SPY"],
                         timeframe="1d", source="synthetic")
    s.commit()
    store.run_account(s, "pair", now=NOW)
    rep = compare(s, "pair")
    assert rep["paper_changes"] > 0 and rep["consistent"] is True, rep["notes"]
    acc = s.scalar(select(PaperAccount).where(PaperAccount.name == "pair"))
    fill = s.scalars(select(PaperFill).where(PaperFill.account_id == acc.id)
                     .order_by(PaperFill.id)).first()
    fill.price *= 1.01  # a paper fill 100 bps away from the bar open
    s.flush()
    bad = compare(s, "pair")
    assert bad["consistent"] is False and bad["max_fill_price_gap_bps"] > 99
    assert any("fill price gap" in n for n in bad["notes"])


def test_create_checks_the_leg_count(seeded):
    with pytest.raises(ValueError, match="needs 2"):
        store.create_account(seeded, "p", strategy_key="STAT-PAIR-001", symbol=["SPY"],
                             timeframe="1d", source="synthetic")
    with pytest.raises(KeyError):
        store.create_account(seeded, "p", strategy_key="NOPE", symbol=["SPY"],
                             timeframe="1d", source="synthetic")
