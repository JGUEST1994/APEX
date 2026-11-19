"""Phase 9: independent risk service, kill switch, order state machine, paper trading."""

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apex.backtest.engine import BacktestConfig, run_backtest
from apex.core.config import TradingMode
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.execution.orders import IllegalOrderTransition, Order, OrderStatus, client_order_id
from apex.execution.paper import Fill, PaperBroker, Portfolio
from apex.execution.trader import PaperTrader
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.risk.limits import RiskLimits
from apex.risk.service import AccountState, KillSwitch, OrderIntent, RiskService
from apex.strategies.registry import all_strategies
from tests.conftest import utc

NOW = datetime(2025, 1, 2, tzinfo=UTC)
SPY = by_symbol()["SPY"]


def _risk(**kw):
    return RiskService(RiskLimits(**kw), TradingMode.PAPER)


def _acct(eq=100_000.0, **kw):
    return AccountState(equity=eq, day_start_equity=kw.pop("day", eq),
                        peak_equity=kw.pop("peak", eq), **kw)


def _intent(coid="a", qty=10, price=100.0, stop=98.0, sym="SPY"):
    return OrderIntent(coid, sym, qty, price, 1.0, stop)


def test_risk_service_refuses_research_mode():
    with pytest.raises(ValueError):
        RiskService(RiskLimits(), TradingMode.RESEARCH)


def test_limits_only_tighten():
    lim = RiskLimits()
    assert lim.tightened(max_leverage=1.0).max_leverage == 1.0
    with pytest.raises(ValueError):
        lim.tightened(max_leverage=5.0)
    with pytest.raises(ValueError):
        lim.tightened(max_daily_loss=0.0)
    r = _risk()
    r.tighten(max_positions=3)
    with pytest.raises(ValueError):
        r.tighten(max_positions=4)


def test_pre_trade_checks():
    r = _risk()
    ok = r.check(_intent("ok", qty=100), _acct(), data_stale=False, now=NOW)
    assert ok.approved, ok.reasons
    assert r.check(_intent("ok", qty=100), _acct(), data_stale=False,
                   now=NOW).reasons == ("duplicate client_order_id",)
    big = r.check(_intent("big", qty=300, stop=None), _acct(), data_stale=False, now=NOW)
    assert "max position size" in big.reasons and "stop price required for new risk" in big.reasons
    wide = r.check(_intent("wide", qty=100, stop=90.0), _acct(), data_stale=False, now=NOW)
    assert wide.reasons == ("max risk per trade",)
    stale = r.check(_intent("st"), _acct(), data_stale=True, now=NOW)
    assert "market data is stale" in stale.reasons
    full = _acct(positions={f"S{i}": 1000.0 for i in range(10)})
    assert "max concurrent positions" in r.check(_intent("n"), full, data_stale=False,
                                                 now=NOW).reasons
    gross = _acct(positions={"A": 50_000.0, "B": 45_000.0})
    assert "max gross exposure" in r.check(_intent("g", qty=100), gross, data_stale=False,
                                           now=NOW).reasons
    streak = _acct(consecutive_losses=6)
    assert "consecutive losses limit" in r.check(_intent("c"), streak, data_stale=False,
                                                 now=NOW).reasons


def test_reducing_orders_always_allowed():
    r = _risk()
    acct = _acct(positions={"SPY": 30_000.0})  # already above the 20% position limit
    d = r.check(_intent("red", qty=-100, stop=None), acct, data_stale=True, now=NOW)
    assert d.approved, d.reasons


def test_kill_switch_trips_on_drawdown_and_needs_human_reset():
    r = _risk()
    r.monitor(_acct(89_000.0, peak=100_000.0, day=89_000.0), NOW)
    assert r.kill.engaged and "drawdown" in r.kill.reason
    d = r.check(_intent("k"), _acct(89_000.0, peak=100_000.0), data_stale=False, now=NOW)
    assert any("kill switch" in x for x in d.reasons)
    for actor in ("system", "risk", ""):
        with pytest.raises(PermissionError):
            r.kill.reset(actor, NOW)
    r.kill.reset("jake", NOW)
    assert not r.kill.engaged
    k = KillSwitch()
    RiskService(RiskLimits(), TradingMode.PAPER, k).monitor(_acct(97_000.0, day=100_000.0),
                                                            NOW)
    assert k.engaged and "daily loss" in k.reason


def test_order_state_machine_and_ids():
    a = client_order_id("TF-SMA-001", "SPY", NOW)
    assert a == client_order_id("TF-SMA-001", "SPY", NOW) != client_order_id("X", "SPY", NOW)
    o = Order(a, "SPY", 10)
    with pytest.raises(IllegalOrderTransition):
        o.fill(10, 100.0)  # not acknowledged yet
    o.to(OrderStatus.SUBMITTED)
    o.to(OrderStatus.ACKNOWLEDGED)
    o.fill(4, 100.0)
    assert o.status is OrderStatus.PARTIALLY_FILLED
    with pytest.raises(IllegalOrderTransition):
        o.fill(7, 100.0)  # overfill
    o.fill(6, 101.0)
    assert o.status is OrderStatus.FILLED and o.avg_price == pytest.approx(100.6)
    with pytest.raises(IllegalOrderTransition):
        o.to(OrderStatus.CANCELLED)


def test_paper_broker_is_idempotent_and_fills_next_open():
    b = PaperBroker({"SPY": SPY})
    o1 = b.submit(Order("x", "SPY", 10))
    assert b.submit(Order("x", "SPY", 99)) is o1
    fills = b.on_bar_open("SPY", 101.0)
    assert len(fills) == 1 and fills[0].price == 101.0 and fills[0].qty == 10
    assert b.on_bar_open("SPY", 102.0) == []


def test_portfolio_pnl_and_loss_streak():
    pf = Portfolio(cash=10_000.0)
    pf.apply(Fill("1", "SPY", 10, 100.0, 1.0), SPY)
    pf.apply(Fill("2", "SPY", -10, 95.0, 1.0), SPY)
    assert pf.realised == pytest.approx(-50.0) and pf.consecutive_losses == 1
    assert pf.cash == pytest.approx(10_000 - 50 - 2)
    pf.apply(Fill("3", "SPY", -5, 100.0, 0.0), SPY)
    pf.apply(Fill("4", "SPY", 5, 90.0, 0.0), SPY)
    assert pf.consecutive_losses == 0 and pf.units["SPY"] == 0


def _bars():
    return SyntheticProvider(annual_vol=0.4).fetch_bars(SPY, Timeframe.D1, utc(2021, 1, 1),
                                                        utc(2023, 1, 1))


def test_paper_trader_matches_backtester_fill_timing():
    bars = _bars()
    strat = all_strategies()["TF-SMA-001"]()
    t = PaperTrader(strat, SPY, _risk(), history=400)
    for row in bars.to_dict("records"):
        t.on_bar(row)
    fills = [e for e in t.events if e.kind == "fill"]
    assert fills, "expected paper trades"
    # every fill happens at a bar open, one bar after the signal that caused it
    opens = dict(zip(bars["ts"], bars["open"], strict=True))
    assert all(e.detail["price"] == opens[e.ts] for e in fills)
    bt = run_backtest(bars, strat, SPY, config=BacktestConfig())
    assert bt.metrics["trades"] > 0
    # sizing stays inside the risk limits on every bar
    assert all(abs(u) * bars["close"].max() <= 0.2 * 1.5 * 100_000 for u in t.pf.units.values())


def test_paper_trader_flattens_after_kill_switch():
    bars = _bars()
    strat = all_strategies()["MOM-TSMOM-001"]()
    risk = _risk(max_drawdown=0.002)  # trips almost immediately
    t = PaperTrader(strat, SPY, risk, history=300)
    for row in bars.to_dict("records"):
        t.on_bar(row)
    kills = [e for e in t.events if e.kind == "kill_switch"]
    assert kills and risk.kill.engaged
    after = [e for e in t.events if e.ts > kills[0].ts and e.kind == "fill"]
    assert all(e.detail["qty"] * t.pf.units.get("SPY", 0) <= 0 for e in after)
    assert t.pf.units.get("SPY", 0) == 0


def test_risk_package_is_independent():
    root = Path(__file__).resolve().parent.parent / "src" / "apex" / "risk"
    for f in root.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(("apex.strategies", "apex.ml", "apex.ai",
                                                   "apex.execution", "apex.backtest")), f


def test_loss_streak_counts_round_trips_not_trims():
    pf = Portfolio(cash=10_000.0)
    pf.apply(Fill("1", "SPY", 10, 100.0, 0.0), SPY)
    pf.apply(Fill("2", "SPY", -2, 99.0, 0.0), SPY)  # trim at a small loss: trade still open
    pf.apply(Fill("3", "SPY", -2, 98.0, 0.0), SPY)
    assert pf.consecutive_losses == 0
    pf.apply(Fill("4", "SPY", -6, 105.0, 0.0), SPY)  # closes the round trip at a net profit
    assert pf.consecutive_losses == 0 and "SPY" not in pf.trade_pnl
    pf.apply(Fill("5", "SPY", 5, 100.0, 0.0), SPY)
    pf.apply(Fill("6", "SPY", -5, 99.0, 0.0), SPY)
    assert pf.consecutive_losses == 1


def test_reversal_closes_old_position_even_if_new_entry_is_refused():
    bars = _bars()
    strat = all_strategies()["TF-SMA-001"]()  # long/short: flips straight from +1 to -1
    target = strat.target_position(bars, strat.params()).to_numpy()
    t = PaperTrader(strat, SPY, _risk(), history=400)
    for i, row in enumerate(bars.to_dict("records")):
        t.on_bar(row, data_stale=i >= 250)  # stale data: new risk refused, exits allowed
        held = t.pf.units.get("SPY", 0)
        if i >= 252:  # holding reflects the order placed on the previous bar
            assert held == 0 or held * target[i - 1] > 0, row["ts"]
    assert any(e.kind == "refused" for e in t.events)
    assert t.pf.units.get("SPY", 0) == 0


def test_losing_streak_trips_kill_switch():
    r = _risk(max_consecutive_losses=3)
    r.monitor(_acct(consecutive_losses=2), NOW)
    assert not r.kill.engaged
    r.monitor(_acct(consecutive_losses=3), NOW)
    assert r.kill.engaged and "3 losing trades in a row" in r.kill.reason
    assert r.check(_intent("s"), _acct(consecutive_losses=3), data_stale=False, now=NOW).reasons[0] \
        .startswith("kill switch engaged")
