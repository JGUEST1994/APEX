"""`apex doctor`: a plain-English health check of an APEX install.

Read-only. It never prints secret values, only whether they are set, and it never changes
the database (it does not run migrations or reset anything; it says which command would).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from apex.core.config import Settings

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass(frozen=True)
class Check:
    level: str  # OK | WARN | FAIL
    name: str
    detail: str


def _migration_heads(url: str, engine) -> tuple[set[str], set[str]]:  # noqa: ANN001
    logging.getLogger("alembic").setLevel(logging.WARNING)  # keep the report readable (set
    # before importing alembic: its plugin setup logs at import time)
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from apex.db.migrate import alembic_config

    heads = set(ScriptDirectory.from_config(alembic_config(url)).get_heads())
    with engine.connect() as conn:
        current = set(MigrationContext.configure(conn).get_current_heads())
    return current, heads


def _backup_check(folder: Path, now: datetime | None = None) -> Check:
    from apex.backup import PREFIX

    files = sorted(folder.glob(f"{PREFIX}*.db")) if folder.is_dir() else []
    if not files:
        return Check(WARN, "Backups", f"none in {folder}; run: apex backup")
    newest = max(f.stat().st_mtime for f in files)
    age = (now or datetime.now(UTC)) - datetime.fromtimestamp(newest, UTC)
    days = age.total_seconds() / 86400
    when = "today" if days < 1 else f"{int(days)} day(s) ago"
    return (Check(OK, "Backups", f"{len(files)} in {folder}, newest {when}") if days <= 7 else
            Check(WARN, "Backups", f"newest is {int(days)} days old; run: apex backup"))


LIVE_SOURCES = ("alpaca", "polygon", "tiingo", "coinbase", "binance", "kraken")  # can be topped up
STALE_DAYS = 7


def _freshness(s, now: datetime) -> Check:  # noqa: ANN001
    """Datasets from sources that `apex update` can top up, whose newest bar is old."""
    from apex.db.models import Instrument, PriceBar

    rows = s.execute(select(Instrument.symbol, PriceBar.timeframe, PriceBar.source,
                            func.max(PriceBar.ts))
                     .join(Instrument, Instrument.id == PriceBar.instrument_id)
                     .where(PriceBar.source.in_(LIVE_SOURCES))
                     .group_by(Instrument.symbol, PriceBar.timeframe, PriceBar.source)).all()
    if not rows:
        return Check(OK, "Price freshness", "no downloaded datasets to keep up to date")
    old = []
    for sym, tf, src, last in rows:
        last = last if last.tzinfo else last.replace(tzinfo=UTC)
        days = (now - last).total_seconds() / 86400
        if days > STALE_DAYS:
            old.append((days, f"{sym} {tf} {src} ({int(days)} days)"))
    if not old:
        return Check(OK, "Price freshness", f"{len(rows)} downloaded dataset(s), all updated "
                     f"within {STALE_DAYS} days")
    names = [n for _, n in sorted(old, reverse=True)[:3]] + (["..."] if len(old) > 3 else [])
    return Check(WARN, "Price freshness", f"{len(old)} of {len(rows)} downloaded dataset(s) "
                 f"more than {STALE_DAYS} days behind: {', '.join(names)}; run apex update "
                 "or start-scheduler.bat")


def run_checks(settings: Settings, env_file: Path | None = Path(".env"),
               backup_folder: Path = Path("backups"),
               now: datetime | None = None) -> list[Check]:
    from apex.db.models import IngestionRun, Instrument, PaperAccount, PriceBar
    from apex.db.session import make_engine, make_session_factory

    now = now or datetime.now(UTC)
    out: list[Check] = []
    py = sys.version_info
    out.append(Check(OK if py >= (3, 11) else FAIL, "Python",
                     f"{py.major}.{py.minor}.{py.micro}" + ("" if py >= (3, 11)
                                                             else " (APEX needs 3.11 or newer)")))
    out.append(Check(OK, "Trading mode",
                     f"{settings.trading_mode.value} (real-money trading is not available "
                     "in this release)"))
    if env_file is not None:
        out.append(Check(OK, "Settings file", f"{env_file} found") if env_file.exists() else
                   Check(WARN, "Settings file", f"no {env_file} here; defaults are used. Copy "
                         ".env.example to .env if you need API keys"))
    for label, *keys in (("Alpaca keys (US stocks)", "alpaca_key_id", "alpaca_secret_key"),
                         ("Polygon key (US stocks)", "polygon_api_key"),
                         ("Tiingo key (US stocks, forex)", "tiingo_api_key")):
        have = all(getattr(settings, k) is not None for k in keys)
        out.append(Check(OK, label, "set" if have else
                         "not set (only needed for that data source)"))
    tok = settings.api_token is not None and bool(settings.api_token.get_secret_value())
    out.append(Check(OK, "Dashboard token", "required" if tok else
                     "not set (fine while APEX stays on this computer)"))
    out.append(Check(OK, "External AI", "off" if settings.ai_provider == "none"
                     else f"on ({settings.ai_provider})"))

    if make_url(settings.database_url).get_backend_name() == "sqlite":
        out.append(_backup_check(backup_folder))

    engine = make_engine(settings.database_url)
    if engine.url.get_backend_name() == "sqlite" and engine.url.database not in (None, "",
                                                                                  ":memory:"):
        if not Path(engine.url.database).exists():  # connecting would create an empty file
            out.append(Check(FAIL, "Database", f"no database file {engine.url.database} "
                             "here; run: apex db-upgrade (in the APEX folder)"))
            engine.dispose()
            return out
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check(FAIL, "Database", f"cannot connect: {type(exc).__name__}"))
        engine.dispose()
        return out
    out.append(Check(OK, "Database", engine.url.render_as_string(hide_password=True)))
    try:
        current, heads = _migration_heads(settings.database_url, engine)
        if current != heads:
            out.append(Check(FAIL, "Database tables", "out of date; run: apex db-upgrade"))
            engine.dispose()
            return out
        out.append(Check(OK, "Database tables", "up to date"))
        with make_session_factory(engine)() as s:
            n_inst = s.scalar(select(func.count()).select_from(Instrument)) or 0
            out.append(Check(OK, "Markets", f"{n_inst} defined") if n_inst else
                       Check(WARN, "Markets", "none yet; run: apex seed-instruments"))
            sets = s.execute(select(PriceBar.instrument_id, PriceBar.timeframe,
                                    PriceBar.source).distinct()).all()
            out.append(Check(OK, "Stored prices", f"{len(sets)} dataset(s)") if sets else
                       Check(WARN, "Stored prices", "none yet; see 'Get some prices' in "
                             "docs/USER_GUIDE.md"))
            out.append(_freshness(s, now))
            last = s.scalars(select(IngestionRun).order_by(IngestionRun.id.desc())
                             .limit(1)).first()
            if last is not None and last.status == "failed":
                out.append(Check(WARN, "Last download", f"run {last.id} ({last.source}) "
                                 "failed; see the Overview tab"))
            halted = s.scalars(select(PaperAccount.name).where(
                PaperAccount.kill_engaged.is_(True) | (PaperAccount.status == "halted"))).all()
            if halted:
                out.append(Check(WARN, "Practice accounts", "stopped by the kill switch: "
                                 + ", ".join(halted) + "; a named person must reset them "
                                 "with apex paper-reset-kill"))
            else:
                n_acc = s.scalar(select(func.count()).select_from(PaperAccount)) or 0
                out.append(Check(OK, "Practice accounts", f"{n_acc}, none stopped"))
    finally:
        engine.dispose()
    return out


def render(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = [f"[{c.level:<4}] {c.name:<{width}}  {c.detail}" for c in checks]
    fails = sum(c.level == FAIL for c in checks)
    warns = sum(c.level == WARN for c in checks)
    lines.append("All good." if not fails and not warns else
                 f"{fails} problem(s), {warns} warning(s).")
    return "\n".join(lines)
