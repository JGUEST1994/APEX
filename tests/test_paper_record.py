"""The 30-day practice record: only forward days count, problems and staleness block it."""

import pytest
from sqlalchemy import delete, select

from apex.cli import main as cli
from apex.db.models import PaperAccount, PaperPosition, PriceBar
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.execution import store
from apex.execution.consistency import compare
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.paper_record import record
from tests.conftest import utc


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
    acc.created_at = created  # pretend it was set up part-way through the stored history
    s.commit()
    store.run_account(s, name, now=utc(2023, 1, 2))
    return acc


def test_long_clean_forward_record_is_ready(seeded):
    _account(seeded, "old", utc(2022, 6, 1))
    rep = record(seeded, "old", now=utc(2023, 1, 2))
    assert rep["ready"] is True, rep["reasons"]
    assert rep["days"] >= 200 and rep["running"] and rep["consistent"] is True


def test_replayed_history_does_not_count_and_stale_data_shows(seeded):
    _account(seeded, "new", utc(2022, 12, 20))
    rep = record(seeded, "new", now=utc(2023, 1, 2))
    assert not rep["ready"] and rep["days"] < 30
    assert "older history replayed" in rep["reasons"][0]
    later = record(seeded, "new", now=utc(2023, 2, 1))
    assert not later["running"] and any("scheduler" in r for r in later["reasons"])
    with pytest.raises(KeyError):
        record(seeded, "nope")


def test_a_halt_blocks_the_record(seeded):
    acc = _account(seeded, "acct", utc(2022, 1, 1))
    pos = seeded.scalar(select(PaperPosition).where(PaperPosition.account_id == acc.id))
    if pos is None:  # make sure there is stored state to corrupt
        pos = PaperPosition(account_id=acc.id, symbol="SPY", units=0.0, avg_price=0.0)
        seeded.add(pos)
    pos.units += 1
    seeded.commit()
    with pytest.raises(store.AccountHalted):
        store.run_account(seeded, "acct", now=utc(2023, 1, 2))
    rep = record(seeded, "acct", now=utc(2023, 1, 2))
    assert not rep["ready"] and len(rep["problem_events"]) >= 1
    assert any("halted" in r for r in rep["reasons"])


def test_cli_reports_not_ready(seeded, db_url, monkeypatch, capsys):
    _account(seeded, "old", utc(2022, 6, 1))
    from apex.core.config import get_settings

    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        rc = cli(["paper-record", "--name", "old"])
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    # wall-clock now is far past the synthetic data, so the CLI reports it as stale
    assert rc == 4 and "NOT READY" in out and "scheduler" in out
    assert "safety limits" not in out  # standard limits are not mentioned


def test_the_record_shows_limits_that_differ_from_the_standard(seeded, db_url, monkeypatch,
                                                                capsys):
    s = seeded
    store.create_account(s, "roomy", strategy_key="TF-SMA-001", symbol="SPY", timeframe="1d",
                         source="synthetic", params={"fast": 10, "slow": 40},
                         limits=store.RiskLimits().changed({"max_positions": 13}), actor="Sam")
    s.commit()
    store.run_account(s, "roomy", now=utc(2023, 1, 2))
    rep = record(s, "roomy", now=utc(2023, 1, 2))
    assert rep["limits_changed"] == {"max_positions": {"standard": 10, "account": 13}}
    _account(s, "plain", utc(2022, 6, 1))
    assert record(s, "plain", now=utc(2023, 1, 2))["limits_changed"] == {}
    from apex.core.config import get_settings

    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        cli(["paper-record", "--name", "roomy"])
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "safety limits differ from the standard: max_positions 13 (standard 10)" in out


def test_a_start_now_account_is_judged_on_its_own_days(seeded):
    seeded.execute(delete(PriceBar).where(PriceBar.ts >= utc(2022, 6, 1)))
    seeded.commit()  # set up on 1 June 2022, before later prices arrived
    acc = store.create_account(seeded, "fresh", strategy_key="TF-SMA-001", symbol="SPY",
                               timeframe="1d", source="synthetic",
                               params={"fast": 10, "slow": 40}, start_now=True)
    acc.created_at = utc(2022, 6, 1)
    seeded.commit()
    ingest_bars(seeded, SyntheticProvider(annual_vol=0.4), by_symbol()["SPY"], Timeframe.D1,
                utc(2021, 1, 1), utc(2023, 1, 1))
    seeded.commit()
    store.run_account(seeded, "fresh", now=utc(2023, 1, 2))
    rep = record(seeded, "fresh", now=utc(2023, 1, 2))
    assert rep["ready"] is True, rep["reasons"]
    own = compare(seeded, "fresh")  # the history before its start is warm-up only
    assert own["consistent"] is True and own["bars"] < 160
    assert "bars after" in own["notes"][0]
    whole = compare(seeded, "fresh", since=utc(2020, 1, 1))  # as if it had to match it all
    assert whole["bars"] > 400 and whole["direction_agreement"] < own["direction_agreement"]
