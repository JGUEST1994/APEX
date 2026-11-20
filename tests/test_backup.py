"""`apex backup`: consistent SQLite copy, checked, with optional pruning."""

import sqlite3
from datetime import UTC, datetime

import pytest

from apex.backup import BackupError, backup
from apex.cli import main as cli
from apex.core.config import get_settings


def _db(path):
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE t (x INTEGER)")
        c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(100)])
    return f"sqlite:///{path}"


def test_copy_matches_and_live_file_is_unchanged(tmp_path):
    url = _db(tmp_path / "apex.db")
    before = (tmp_path / "apex.db").read_bytes()
    dest = backup(url, tmp_path / "backups", now=datetime(2026, 11, 20, 3, 4, 5, tzinfo=UTC))
    assert dest.name == "apex-backup-20261120-030405.db"
    with sqlite3.connect(dest) as c:
        assert c.execute("SELECT count(*), sum(x) FROM t").fetchone() == (100, 4950)
    assert (tmp_path / "apex.db").read_bytes() == before
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_keep_prunes_only_old_apex_backups(tmp_path):
    url = _db(tmp_path / "apex.db")
    folder = tmp_path / "backups"
    folder.mkdir()
    (folder / "notes.txt").write_text("mine")
    for d in range(1, 5):
        backup(url, folder, keep=2, now=datetime(2026, 11, d, tzinfo=UTC))
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["apex-backup-20261103-000000.db", "apex-backup-20261104-000000.db",
                     "notes.txt"]
    with pytest.raises(BackupError, match="already exists"):
        backup(url, folder, now=datetime(2026, 11, 4, tzinfo=UTC))


@pytest.mark.parametrize("url,msg", [
    ("postgresql+psycopg://u:p@localhost/x", "pg_dump"),
    ("sqlite://", "in-memory"),
])
def test_refuses_what_it_cannot_copy(url, msg):
    with pytest.raises(BackupError, match=msg):
        backup(url, None)


def test_cli_missing_file_and_success(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APEX_DATABASE_URL", f"sqlite:///{tmp_path / 'none.db'}")
    get_settings.cache_clear()
    try:
        assert cli(["backup"]) == 1
        assert "nothing to back up" in capsys.readouterr().err
        assert not (tmp_path / "none.db").exists()
        monkeypatch.setenv("APEX_DATABASE_URL", _db(tmp_path / "apex.db"))
        get_settings.cache_clear()
        assert cli(["backup", "--keep", "3"]) == 0
    finally:
        get_settings.cache_clear()
    assert "checked OK" in capsys.readouterr().out
    assert len(list((tmp_path / "backups").glob("apex-backup-*.db"))) == 1
