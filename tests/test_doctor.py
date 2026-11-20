"""`apex doctor`: read-only install check."""

import pytest
from pydantic import SecretStr

from apex.cli import main as cli
from apex.core.config import Settings, get_settings
from apex.db.migrate import downgrade
from apex.db.models import PaperAccount
from apex.doctor import FAIL, OK, WARN, render, run_checks


def _levels(checks):
    return {c.name: c.level for c in checks}


def test_fresh_database_warns_about_missing_markets_and_data(db_url, tmp_path):
    env = tmp_path / ".env"
    checks = run_checks(Settings(_env_file=None, database_url=db_url), env_file=env)
    lv = _levels(checks)
    assert lv["Database"] == OK and lv["Database tables"] == OK
    assert lv["Markets"] == WARN and lv["Stored prices"] == WARN
    assert lv["Settings file"] == WARN and lv["Practice accounts"] == OK
    assert FAIL not in lv.values()
    env.write_text("")
    assert _levels(run_checks(Settings(_env_file=None, database_url=db_url),
                              env_file=env))["Settings file"] == OK


def test_seeded_data_halted_account_and_secrets_never_printed(db_url, session):
    from apex.domain.instruments import by_symbol
    from apex.domain.market import Timeframe
    from apex.marketdata.ingest import ingest_bars
    from apex.marketdata.providers.synthetic import SyntheticProvider
    from tests.conftest import utc

    ingest_bars(session, SyntheticProvider(), by_symbol()["SPY"], Timeframe.D1,
                utc(2024, 1, 1), utc(2024, 3, 1))
    session.add(PaperAccount(name="acct-1", strategy_key="TF-SMA-001", symbol="SPY",
                             timeframe="1d", source="synthetic", initial_capital=1e5,
                             cash=1e5, peak_equity=1e5, day_start_equity=1e5,
                             kill_engaged=True, status="halted"))
    session.commit()
    s = Settings(_env_file=None, database_url=db_url,
                 alpaca_key_id=SecretStr("id-sekrit"), alpaca_secret_key=SecretStr("sk-sekrit"))
    checks = run_checks(s, env_file=None)
    lv = _levels(checks)
    assert lv["Markets"] == OK and lv["Stored prices"] == OK
    assert lv["Practice accounts"] == WARN
    text = render(checks)
    assert "acct-1" in text and "paper-reset-kill" in text
    assert "sekrit" not in text and "Alpaca keys (US stocks)  set" in text


def test_out_of_date_tables_fail(db_url):
    downgrade(db_url, "0005")
    checks = run_checks(Settings(_env_file=None, database_url=db_url), env_file=None)
    assert checks[-1].level == FAIL and "apex db-upgrade" in checks[-1].detail


def test_missing_sqlite_file_fails_without_creating_it(tmp_path, monkeypatch, capsys):
    path = tmp_path / "nothing.db"
    monkeypatch.setenv("APEX_DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    try:
        assert cli(["doctor"]) == 1
    finally:
        get_settings.cache_clear()
    assert not path.exists()
    out = capsys.readouterr().out
    assert "[FAIL] Database" in out and "1 problem(s)" in out


@pytest.mark.parametrize("live", ["live", "real"])
def test_live_mode_cannot_even_be_configured(live):
    with pytest.raises(ValueError):
        Settings(_env_file=None, trading_mode=live)


def test_backup_check_counts_age(tmp_path):
    import os
    from datetime import UTC, datetime, timedelta

    from apex.doctor import _backup_check

    folder = tmp_path / "backups"
    assert _backup_check(folder).level == WARN
    folder.mkdir()
    f = folder / "apex-backup-20261101-000000.db"
    f.write_bytes(b"")
    (folder / "other.db").write_bytes(b"")
    now = datetime.now(UTC)
    c = _backup_check(folder, now)
    assert c.level == OK and "1 in" in c.detail and "today" in c.detail
    old = (now - timedelta(days=10)).timestamp()
    os.utime(f, (old, old))
    assert _backup_check(folder, now).level == WARN
    assert "apex backup" in _backup_check(folder, now).detail
