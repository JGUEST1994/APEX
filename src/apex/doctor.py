"""`apex doctor`: a plain-English health check of an APEX install.

Read-only. It never prints secret values, only whether they are set, and it never changes
the database (it does not run migrations or reset anything; it says which command would).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select, text

from apex.core.config import Settings

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass(frozen=True)
class Check:
    level: str  # OK | WARN | FAIL
    name: str
    detail: str


def _migration_heads(url: str, engine) -> tuple[set[str], set[str]]:  # noqa: ANN001
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from apex.db.migrate import alembic_config

    logging.getLogger("alembic").setLevel(logging.WARNING)  # keep the report readable
    heads = set(ScriptDirectory.from_config(alembic_config(url)).get_heads())
    with engine.connect() as conn:
        current = set(MigrationContext.configure(conn).get_current_heads())
    return current, heads


def run_checks(settings: Settings, env_file: Path | None = Path(".env")) -> list[Check]:
    from apex.db.models import IngestionRun, Instrument, PaperAccount, PriceBar
    from apex.db.session import make_engine, make_session_factory

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
                         ("Polygon key (US stocks)", "polygon_api_key")):
        have = all(getattr(settings, k) is not None for k in keys)
        out.append(Check(OK, label, "set" if have else
                         "not set (only needed for that data source)"))
    out.append(Check(OK, "External AI", "off" if settings.ai_provider == "none"
                     else f"on ({settings.ai_provider})"))

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
