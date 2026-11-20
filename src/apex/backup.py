"""`apex backup`: a safe copy of the SQLite database while APEX may be running.

Uses SQLite's online backup API (a consistent snapshot even if the dashboard or scheduler
is writing), then checks the copy with ``PRAGMA integrity_check``. Read-only for the live
database. PostgreSQL installs use ``pg_dump`` instead (see docs/DEPLOYMENT.md).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

PREFIX = "apex-backup-"


class BackupError(RuntimeError):
    pass


def sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise BackupError("this database is not SQLite; back it up with pg_dump "
                          "(see docs/DEPLOYMENT.md, 'Backups')")
    if url.database in (None, "", ":memory:"):
        raise BackupError("an in-memory database cannot be backed up")
    path = Path(url.database)
    if not path.exists():
        raise BackupError(f"no database file {path} here; nothing to back up")
    return path


def backup(database_url: str, folder: Path, keep: int | None = None,
           now: datetime | None = None) -> Path:
    """Copy the database into ``folder`` as ``apex-backup-YYYYMMDD-HHMMSS.db`` (UTC).

    ``keep`` deletes the oldest APEX backups in that folder beyond the newest ``keep``;
    other files are never touched.
    """
    src = sqlite_path(database_url)
    if keep is not None and keep < 1:
        raise BackupError("--keep must be at least 1")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    dest = folder / f"{PREFIX}{stamp}.db"
    if dest.exists():
        raise BackupError(f"{dest} already exists")
    if dest.resolve() == src.resolve():
        raise BackupError("the backup would overwrite the database itself")
    tmp = dest.with_suffix(".partial")
    try:
        with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as live, \
                sqlite3.connect(tmp) as copy:
            live.backup(copy)
            ok = copy.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise BackupError(f"the copy failed its integrity check: {ok}")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    if keep is not None:
        for old in sorted(folder.glob(f"{PREFIX}*.db"))[:-keep]:
            old.unlink()
    return dest
