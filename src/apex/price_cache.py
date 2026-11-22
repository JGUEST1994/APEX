"""Keep downloaded prices between research runs (research only).

Free price services limit requests (Tiingo allows about 50 an hour), so the research run
keeps a copy of its database holding prices only, and a later run reuses every download
whose exact request (provider, market, timeframe, start, end) is already stored. Research
results are stripped from the copy: a reused database must not carry earlier experiments,
which would add to the luck test's try counts.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from apex.db.models import IngestionRun, Instrument

# Everything that is not prices or instruments: research results, approvals and the audit
# trail, practice accounts and research notes. Children before parents.
RESULT_TABLES = ("experiments", "strategy_status_changes", "strategy_versions", "strategies",
                 "audit_log", "paper_fills", "paper_orders", "paper_positions",
                 "paper_accounts", "research_journal", "research_chunk_vectors",
                 "research_chunks", "research_documents")


def stored_run(session: Session, source: str, symbol: str, timeframe: str, start: datetime,
               end: datetime) -> IngestionRun | None:
    """The latest successful download of exactly this request, if any."""
    return session.scalars(
        select(IngestionRun).join(Instrument, Instrument.id == IngestionRun.instrument_id)
        .where(IngestionRun.source == source, Instrument.symbol == symbol,
               IngestionRun.timeframe == timeframe, IngestionRun.range_start == start,
               IngestionRun.range_end == end, IngestionRun.status.in_(("ok", "warn")))
        .order_by(IngestionRun.id.desc())).first()


def sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("the price cache works with a SQLite database file only")
    return Path(url.database)


def save_prices_only(database_url: str, out: Path) -> int:
    """Copy the database to ``out`` without research results. Returns the bars kept."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(sqlite_path(database_url))
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
        tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in RESULT_TABLES:
            if t in tables:
                dst.execute(f"DELETE FROM {t}")  # noqa: S608 - fixed table names
        dst.commit()
        bars = dst.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
        dst.execute("VACUUM")
    finally:
        src.close()
        dst.close()
    tmp.replace(out)
    return int(bars)
