"""Phase 9 slice 2: paper account persistence, restart equivalence, reconciliation."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from apex.cli import main as cli
from apex.db.models import AuditEvent, PaperAccount, PaperFill, PaperOrder, PaperPosition
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
