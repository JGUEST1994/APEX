"""Phase 9: one paper account trading several instruments with shared cash and risk."""

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
from apex.strategies.registry import all_strategies
from tests.conftest import utc

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SYMS = by_symbol()


def _bars(close, start="2021-01-01"):
    close = np.asarray(close, float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"ts": pd.date_range(start, periods=len(close), freq="1D", tz="UTC"),
                         "open": open_, "high": np.maximum(open_, close) * 1.002,
                         "low": np.minimum(open_, close) * 0.998, "close": close,
                         "volume": 1.0})


def _run(trader, frames):
    rows = {s: f.to_dict("records") for s, f in frames.items()}
    for i in range(len(next(iter(rows.values())))):
        trader.on_bars({s: r[i] for s, r in rows.items()})


def test_correlated_instruments_share_one_exposure_budget():
    rng = np.random.default_rng(5)
    trend = np.cumsum(0.002 + rng.normal(0, 0.01, 400))
    spy = 100 * np.exp(trend + rng.normal(0, 0.001, 400))
    qqq = 300 * np.exp(trend + rng.normal(0, 0.001, 400))  # moves with SPY
    gld = 150 * np.exp(np.cumsum(0.002 + rng.normal(0, 0.01, 400)))  # independent of SPY/QQQ
    frames = {"SPY": _bars(spy), "QQQ": _bars(qqq), "AAPL": _bars(gld)}
    specs = [SYMS[s] for s in frames]
    strat = all_strategies()["TF-SMA-001"]()
    risk = RiskService(RiskLimits(max_cluster_exposure=0.25), TradingMode.PAPER)
    t = PaperTrader(strat, specs, risk, history=300)
    _run(t, frames)
    clusters = t._clusters()
    assert clusters["SPY"] == clusters["QQQ"] != clusters["AAPL"]
    refused = [e for e in t.events if e.kind == "refused"
               and "max correlated exposure" in e.detail["reasons"]]
    assert refused and {e.detail["symbol"] for e in refused} <= {"SPY", "QQQ"}
    # the group never holds more than the cap (plus one bar of price drift)
    px = t.last_prices()
    group = sum(abs(t.pf.units.get(s, 0)) * px[s] for s in ("SPY", "QQQ"))
    eq = t.pf.equity(px, {s: SYMS[s] for s in px})
    assert group / eq <= 0.25 * 1.1
    assert t.pf.units.get("AAPL", 0) != 0  # the independent market still trades


def test_single_and_multi_paths_agree_for_one_instrument():
    bars = _bars(100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, 300))))
    strat = all_strategies()["TF-SMA-001"]()
    a = PaperTrader(strat, SYMS["SPY"], RiskService(RiskLimits(), TradingMode.PAPER))
    b = PaperTrader(strat, [SYMS["SPY"]], RiskService(RiskLimits(), TradingMode.PAPER))
    for row in bars.to_dict("records"):
        a.on_bar(row)
        b.on_bars({"SPY": row})
    assert [e.detail for e in a.events] == [e.detail for e in b.events]


@pytest.fixture
def seeded(session):
    p = SyntheticProvider(annual_vol=0.4)
    for sym in ("SPY", "QQQ", "BTCUSDT"):
        ingest_bars(session, p, SYMS[sym], Timeframe.D1, utc(2021, 1, 1), utc(2023, 1, 1))
    session.commit()
    return session


def test_multi_account_restart_reconcile_and_compare(seeded):
    s = seeded
    for name in ("straight", "restarted"):
        store.create_account(s, name, strategy_key="TF-SMA-001",
                             symbol=["SPY", "QQQ", "BTCUSDT"], timeframe="1d",
                             source="synthetic", params={"fast": 10, "slow": 40})
    s.commit()
    store.run_account(s, "straight", now=NOW)
    store.run_account(s, "restarted", now=NOW, max_bars=300)
    store.run_account(s, "restarted", now=NOW)

    def ledger(name):
        acc = s.scalar(select(PaperAccount).where(PaperAccount.name == name))
        return acc, [(f.ts, f.symbol, f.qty, round(f.price, 8)) for f in s.scalars(
            select(PaperFill).where(PaperFill.account_id == acc.id).order_by(PaperFill.id))]

    a, fa = ledger("straight")
    b, fb = ledger("restarted")
    assert fa == fb and {f[1] for f in fa} == {"SPY", "QQQ", "BTCUSDT"}
    assert a.cash == pytest.approx(b.cash)
    assert store.reconcile(s, a) == [] and a.all_symbols == ["SPY", "QQQ", "BTCUSDT"]
    # crypto trades at weekends, stocks do not: a weekend bar only carries crypto
    weekend = [f for f in fa if f[0].weekday() >= 5]
    assert weekend and {f[1] for f in weekend} == {"BTCUSDT"}
    rep = compare(s, "straight")
    assert set(rep["per_symbol"]) == {"SPY", "QQQ", "BTCUSDT"}
    assert all(p["max_fill_price_gap_bps"] == 0.0 for p in rep["per_symbol"].values())
    assert rep["direction_agreement"] > 0.8
    assert any("account-wide limits" in n for n in rep["notes"])


def test_create_rejects_unknown_symbol(seeded):
    from apex.core.errors import UnknownInstrumentError

    with pytest.raises(UnknownInstrumentError):
        store.create_account(seeded, "x", strategy_key="TF-SMA-001", symbol=["SPY", "NOPE"],
                             timeframe="1d", source="synthetic")
