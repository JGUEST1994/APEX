"""Approving a strategy: named person only, READY practice record only, validated only."""

import pytest
from sqlalchemy import select

from apex.approval import ApprovalRefused, approve
from apex.backtest.experiments import ensure_strategy_version
from apex.cli import main as cli
from apex.db.models import AuditEvent, PaperAccount, StrategyStatusChange
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.execution import store
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.strategies.base import StrategyStatus
from apex.strategies.lifecycle import transition
from apex.strategies.registry import all_strategies
from tests.conftest import utc

NOW = utc(2023, 1, 2)


@pytest.fixture
def seeded(session):
    ingest_bars(session, SyntheticProvider(annual_vol=0.4), by_symbol()["SPY"], Timeframe.D1,
                utc(2021, 1, 1), utc(2023, 1, 1))
    session.commit()
    return session


def _account(s, name, created):
    store.create_account(s, name, strategy_key="TF-SMA-001", symbol="SPY", timeframe="1d",
                         source="synthetic", params={"fast": 10, "slow": 40})
    acc = s.scalar(select(PaperAccount).where(PaperAccount.name == name))
    acc.created_at = created
    s.commit()
    store.run_account(s, name, now=NOW)


def _validated(s):
    ver = ensure_strategy_version(s, all_strategies()["TF-SMA-001"]())
    transition(s, ver, StrategyStatus.BACKTESTING, reason="test")
    transition(s, ver, StrategyStatus.VALIDATION, reason="tournament gate passed")
    return ver


def test_named_person_approves_a_validated_strategy_with_a_ready_record(seeded):
    s = seeded
    _account(s, "old", utc(2022, 6, 1))
    ver = _validated(s)
    with pytest.raises(PermissionError):
        approve(s, "old", by="system", now=NOW)
    approve(s, "old", by="Jake", reason="looks sound", now=NOW)
    assert ver.status == "approved"
    steps = s.scalars(select(StrategyStatusChange).where(
        StrategyStatusChange.strategy_version_id == ver.id)
        .order_by(StrategyStatusChange.id)).all()
    assert [x.to_status for x in steps][-2:] == ["paper_trading", "approved"]
    assert steps[-1].actor == "Jake" and "practice account old" in steps[-1].reason
    assert s.scalar(select(AuditEvent).where(AuditEvent.actor == "Jake"))
    with pytest.raises(ApprovalRefused, match="already approved"):
        approve(s, "old", by="Jake", now=NOW)


def test_refused_without_a_ready_record_or_validation(seeded):
    s = seeded
    _account(s, "new", utc(2022, 12, 20))
    _validated(s)
    with pytest.raises(ApprovalRefused, match="not ready"):
        approve(s, "new", by="Jake", now=NOW)
    _account(s, "old", utc(2022, 6, 1))
    ver = ensure_strategy_version(s, all_strategies()["TF-SMA-001"]())
    ver.status = "backtesting"  # failed the tournament gate
    s.flush()
    with pytest.raises(ApprovalRefused, match="validation gate"):
        approve(s, "old", by="Jake", now=NOW)
    with pytest.raises(KeyError):
        approve(s, "nope", by="Jake", now=NOW)


def test_cli_refuses_a_record_that_is_not_ready(seeded, db_url, monkeypatch, capsys):
    _account(seeded, "old", utc(2022, 6, 1))
    _validated(seeded)
    seeded.commit()
    from apex.core.config import get_settings

    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:  # wall-clock now is long after the synthetic prices, so the record is stale
        rc = cli(["strategy-approve", "--account", "old", "--by", "Jake"])
    finally:
        get_settings.cache_clear()
    assert rc == 4 and "not approved" in capsys.readouterr().err
