"""Phase 9 slice 2: paper account persistence, restart equivalence, reconciliation."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from apex.cli import main as cli
from apex.db.models import (
    AuditEvent,
    PaperAccount,
    PaperFill,
    PaperOrder,
    PaperPosition,
    PriceBar,
)
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.execution import store
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from tests.conftest import utc

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def seeded(session):
    ingest_bars(session, SyntheticProvider(annual_vol=0.4), by_symbol()["SPY"], Timeframe.D1,
                utc(2021, 1, 1), utc(2023, 1, 1))
    session.commit()
    return session


def _create(s, name):
    store.create_account(s, name, strategy_key="TF-SMA-001", symbol="SPY", timeframe="1d",
                         source="synthetic", params={"fast": 10, "slow": 40})
    s.commit()


def _ledger(s, name):
    acc = s.scalar(select(PaperAccount).where(PaperAccount.name == name))
    fills = s.scalars(select(PaperFill).where(PaperFill.account_id == acc.id)
                      .order_by(PaperFill.id)).all()
    return acc, [(f.ts, f.qty, round(f.price, 8)) for f in fills]


def test_restart_gives_identical_results(seeded):
    s = seeded
    _create(s, "straight")
    _create(s, "restarted")
    store.run_account(s, "straight", now=NOW)
    first = store.run_account(s, "restarted", now=NOW, max_bars=250)
    assert first["bars"] == 250
    store.run_account(s, "restarted", now=NOW)  # fresh trader rebuilt from the database
    a, fa = _ledger(s, "straight")
    b, fb = _ledger(s, "restarted")
    assert fa and fa == fb
    assert a.cash == pytest.approx(b.cash) and a.last_bar_ts == b.last_bar_ts
    assert store.run_account(s, "straight", now=NOW)["bars"] == 0  # nothing new, no dupes
    assert store.reconcile(s, a) == [] and store.reconcile(s, b) == []
    coids = s.scalars(select(PaperOrder.client_order_id)).all()
    assert len(coids) == len(set(coids))


def test_tampered_state_halts_until_human_reset(seeded):
    s = seeded
    _create(s, "acct")
    store.run_account(s, "acct", now=NOW, max_bars=300)
    acc = s.scalar(select(PaperAccount).where(PaperAccount.name == "acct"))
    pos = s.scalar(select(PaperPosition).where(PaperPosition.account_id == acc.id))
    assert pos is not None
    pos.units += 1  # corrupt stored state
    s.commit()
    with pytest.raises(store.AccountHalted):
        store.run_account(s, "acct", now=NOW)
    s.refresh(acc)
    assert acc.status == "halted" and acc.kill_engaged and "reconciliation" in acc.kill_reason
    with pytest.raises(store.AccountHalted):  # stays halted on every later run
        store.run_account(s, "acct", now=NOW)
    with pytest.raises(store.AccountHalted):  # cannot reset while the books disagree
        store.reset_kill_switch(s, "acct", "jake", NOW)
    pos.units -= 1
    with pytest.raises(PermissionError):
        store.reset_kill_switch(s, "acct", "system", NOW)
    store.reset_kill_switch(s, "acct", "jake", NOW)
    s.commit()
    assert store.run_account(s, "acct", now=NOW)["bars"] > 0
    actions = set(s.scalars(select(AuditEvent.action)).all())
    assert {"paper_account.halt", "kill_switch.reset"} <= actions


def test_kill_switch_state_survives_restart(seeded):
    s = seeded
    store.create_account(s, "tight", strategy_key="MOM-TSMOM-001", symbol="SPY",
                         timeframe="1d", source="synthetic",
                         limits=store.RiskLimits(max_drawdown=0.001))
    s.commit()
    store.run_account(s, "tight", now=NOW, max_bars=400)
    acc = s.scalar(select(PaperAccount).where(PaperAccount.name == "tight"))
    assert acc.kill_engaged
    out = store.run_account(s, "tight", now=NOW)
    assert out["kill_switch"] and out["units"].get("SPY", 0) == 0


def test_paper_matches_backtest(seeded):
    from apex.execution.consistency import compare

    s = seeded
    _create(s, "cmp")
    assert compare(s, "cmp")["consistent"] is None  # not run yet
    store.run_account(s, "cmp", now=NOW, max_bars=200)
    store.run_account(s, "cmp", now=NOW)  # restart in the middle changes nothing
    rep = compare(s, "cmp")
    assert rep["consistent"] and rep["direction_agreement"] == 1.0, rep
    assert rep["backtest_changes"] == rep["paper_changes"] > 2
    assert rep["max_fill_price_gap_bps"] == 0.0 and rep["simulated"] is True
    with pytest.raises(KeyError):
        compare(s, "nope")


def test_compare_flags_and_explains_divergence(seeded):
    from apex.execution.consistency import compare

    s = seeded
    store.create_account(s, "streak", strategy_key="TF-SMA-001", symbol="SPY",
                         timeframe="1d", source="synthetic", params={"fast": 10, "slow": 40},
                         limits=store.RiskLimits(max_consecutive_losses=1))
    s.commit()
    store.run_account(s, "streak", now=NOW)
    rep = compare(s, "streak")
    assert rep["consistent"] is False and rep["missed_changes"] > 0
    assert any("losing trades in a row" in n for n in rep["notes"])
    acc = s.scalar(select(PaperAccount).where(PaperAccount.name == "streak"))
    assert acc.kill_engaged and "losing trades in a row" in acc.kill_reason
    store.reset_kill_switch(s, "streak", "jake", NOW)  # a named human restarts it
    s.commit()
    assert acc.consecutive_losses == 0 and not acc.kill_engaged
    ev = s.scalars(select(AuditEvent).where(AuditEvent.action == "kill_switch.reset")).all()
    assert "cleared_losing_streak" in ev[-1].detail


def test_unknown_strategy_or_params_rejected(seeded):
    with pytest.raises(KeyError):
        store.create_account(seeded, "x", strategy_key="NOPE", symbol="SPY", timeframe="1d",
                             source="synthetic")
    with pytest.raises(ValueError):
        store.create_account(seeded, "x", strategy_key="TF-SMA-001", symbol="SPY",
                             timeframe="1d", source="synthetic", params={"bogus": 1})


def test_cli_paper_requires_paper_mode(db_url, monkeypatch, capsys):
    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    from apex.core.config import get_settings
    get_settings.cache_clear()
    try:
        assert cli(["ingest", "--provider", "synthetic", "--symbol", "SPY", "--timeframe",
                    "1d", "--start", "2022-01-01", "--end", "2023-01-01"]) == 0
        args = ["paper-create", "--name", "p1", "--strategy", "TF-SMA-001", "--symbol", "SPY",
                "--source", "synthetic"]
        assert cli(args) == 2  # research mode refuses
        monkeypatch.setenv("APEX_TRADING_MODE", "paper")
        get_settings.cache_clear()
        assert cli(args) == 0
        assert cli(["paper-run", "--name", "p1"]) == 0
        assert cli(["paper-reset-kill", "--name", "p1", "--actor", "jake"]) == 0
        assert cli(["paper-compare", "--name", "p1"]) in (0, 4)
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "PAPER (SIMULATED)" in out and "simulated=True" in out
    assert "direction_agreement" in out


def test_start_now_trades_only_prices_that_arrive_later(seeded):
    s = seeded
    later = s.execute(delete(PriceBar).where(PriceBar.ts >= utc(2022, 6, 1))).rowcount
    s.commit()  # pretend those prices have not arrived yet when the account is set up
    acc = store.create_account(s, "fresh", strategy_key="TF-SMA-001", symbol="SPY",
                               timeframe="1d", source="synthetic",
                               params={"fast": 10, "slow": 40}, start_now=True)
    s.commit()
    start = acc.last_bar_ts
    assert start < utc(2022, 6, 1) and acc.starts_after == start
    assert store.run_account(s, "fresh", now=NOW)["bars"] == 0  # no history replayed
    ingest_bars(s, SyntheticProvider(annual_vol=0.4), by_symbol()["SPY"], Timeframe.D1,
                utc(2021, 1, 1), utc(2023, 1, 1))  # the same prices, now arriving
    s.commit()
    assert store.run_account(s, "fresh", now=NOW)["bars"] == later
    fills = s.scalars(select(PaperFill).where(PaperFill.account_id == acc.id)).all()
    assert fills and min(f.ts for f in fills) > start
    audit = s.scalars(select(AuditEvent).where(AuditEvent.target == "fresh")).first()
    assert audit.detail["starts_after"] == start.isoformat()


def test_start_now_needs_stored_prices_for_every_instrument(seeded):
    with pytest.raises(ValueError, match="no stored 1d prices from alpaca for SPY"):
        store.create_account(seeded, "x", strategy_key="TF-SMA-001", symbol="SPY",
                             timeframe="1d", source="alpaca", start_now=True)


def test_looser_limits_need_a_named_person_and_are_audited(seeded):
    s = seeded
    looser = store.RiskLimits().changed({"max_positions": 13})
    for nobody in ("system", "", "  Scheduler "):
        with pytest.raises(PermissionError, match="max_positions.*named person"):
            store.create_account(s, "x", strategy_key="TF-SMA-001", symbol="SPY",
                                 timeframe="1d", source="synthetic", limits=looser,
                                 actor=nobody)
    tighter = store.RiskLimits().changed({"max_drawdown": 0.05})  # tightening needs no name
    store.create_account(s, "careful", strategy_key="TF-SMA-001", symbol="SPY",
                         timeframe="1d", source="synthetic", limits=tighter)
    acc = store.create_account(s, "roomy", strategy_key="TF-SMA-001", symbol="SPY",
                               timeframe="1d", source="synthetic", limits=looser, actor="Sam")
    s.commit()
    assert acc.limits["max_positions"] == 13 and acc.limits["max_drawdown"] == 0.10
    audit = s.scalars(select(AuditEvent).where(AuditEvent.target == "roomy")).one()
    assert audit.actor == "Sam" and audit.detail["limits_loosened"] == ["max_positions"]
    assert audit.detail["limits_changed"] == {"max_positions": {"standard": 10, "account": 13}}
    plain = s.scalars(select(AuditEvent).where(AuditEvent.target == "careful")).one()
    assert plain.detail["limits_loosened"] == []
    assert plain.detail["limits_changed"]["max_drawdown"] == {"standard": 0.1, "account": 0.05}
    rep = store.run_account(s, "roomy", now=NOW)  # the account trades under its own limits
    assert rep["bars"] > 0


def test_cli_paper_create_with_settings_and_start_now(db_url, monkeypatch, capsys):
    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    monkeypatch.setenv("APEX_TRADING_MODE", "paper")
    from apex.core.config import get_settings
    get_settings.cache_clear()
    try:
        assert cli(["ingest", "--provider", "synthetic", "--symbol", "SPY", "--timeframe",
                    "1d", "--start", "2022-01-01", "--end", "2023-01-01"]) == 0
        base = ["paper-create", "--strategy", "TF-SMA-001", "--symbol", "SPY",
                "--source", "synthetic"]
        assert cli([*base, "--name", "p2", "--start-now", "--param", "fast=12",
                    "--param", "slow=50"]) == 0
        assert cli(["paper-run", "--name", "p2"]) == 0  # nothing new since it started
        assert cli([*base, "--name", "p3", "--param", "nope=1"]) == 2
        assert cli([*base, "--name", "p4", "--source", "alpaca", "--start-now"]) == 2
        assert cli([*base, "--name", "p5", "--start-now", "--limit", "max_positions=13",
                    "--by", "Sam"]) == 0
        assert cli([*base, "--name", "p6", "--limit", "max_positions=13"]) == 2  # no name
        assert cli([*base, "--name", "p7", "--limit", "max_positions=many", "--by", "Sam"]) == 2
        assert cli([*base, "--name", "p8", "--limit", "max_postions=13", "--by", "Sam"]) == 2
        assert cli([*base, "--name", "p9", "--limit", "max_drawdown=0.05"]) == 0  # tighter
    finally:
        get_settings.cache_clear()
    out, err = capsys.readouterr()
    assert "created paper account p2 (SIMULATED), trading prices after 2022-12-" in out
    assert "bars=0" in out
    assert "unknown parameters" in err and "no stored 1d prices from alpaca" in err
    assert ("safety limits for this account only: max_positions 13 (standard 10); set by Sam, "
            "recorded in the audit log") in out
    assert "safety limits for this account only: max_drawdown 0.05 (standard 0.1)\n" in out
    assert "cannot create p6: looser safety limits (max_positions)" in err
    assert "named person (paper-create --by NAME)" in err
    assert "cannot create p7: max_positions must be a positive number, not 'many'" in err
    assert "cannot create p8: unknown safety limit 'max_postions'" in err
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    with Session(create_engine(db_url)) as s:
        acc = s.scalar(select(PaperAccount).where(PaperAccount.name == "p2"))
        assert acc.params == {"fast": 12, "slow": 50} and acc.starts_after is not None
        assert acc.limits["max_positions"] == 10
        acc = s.scalar(select(PaperAccount).where(PaperAccount.name == "p5"))
        assert acc.limits["max_positions"] == 13
        assert s.scalar(select(PaperAccount).where(PaperAccount.name == "p6")) is None
