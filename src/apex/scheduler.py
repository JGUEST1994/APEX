"""In-process scheduler: keep stored bars fresh and run paper accounts unattended.

A small, dependency-free loop driven by a JSON file::

    {"jobs": [
      {"name": "crypto-1h", "type": "update", "provider": "kraken",
       "symbols": ["BTCUSDT"], "timeframe": "1h", "backfill_start": "2024-01-01",
       "every_minutes": 60},
      {"name": "paper", "type": "paper", "accounts": ["mix"], "every_minutes": 60},
      {"name": "nightly-backup", "type": "backup", "folder": "backups", "keep": 14,
       "every_minutes": 1440}
    ]}

Each cycle runs the jobs that are due, data updates before paper runs so accounts see the
newest bars, and backups (SQLite only) last. A failing job is logged and audited and never
stops the others. Paper jobs run only in paper mode; a halted account stays halted until a
named human resets it.
Every step is idempotent (incremental updates, deterministic order ids), so a restart or
an overlapping cron run cannot duplicate data or orders.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from apex.core.config import Settings, TradingMode
from apex.db.models import AuditEvent

log = logging.getLogger(__name__)
KINDS = ("update", "paper", "backup")


@dataclass
class Job:
    name: str
    type: str
    every: timedelta
    spec: dict
    next_due: datetime | None = None


@dataclass
class Scheduler:
    jobs: list[Job]
    settings: Settings
    factory: sessionmaker[Session]
    provider_factory: Callable[[str], object] | None = None
    history: list[dict] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path, settings: Settings,
                  factory: sessionmaker[Session], **kw: object) -> Scheduler:
        return cls(parse_jobs(json.loads(Path(path).read_text())), settings, factory, **kw)

    def due(self, now: datetime) -> list[Job]:
        jobs = [j for j in self.jobs if j.next_due is None or j.next_due <= now]
        return sorted(jobs, key=lambda j: KINDS.index(j.type))  # data before paper

    def run_cycle(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(UTC)
        results = []
        for job in self.due(now):
            try:
                detail = self._run(job, now)
                status = "ok" if not detail.get("errors") else "partial"
            except Exception as exc:  # noqa: BLE001 - one job must not stop the others
                log.exception("scheduled job %s failed", job.name)
                detail, status = {"error": f"{type(exc).__name__}: {exc}"}, "failed"
            job.next_due = now + job.every
            res = {"job": job.name, "type": job.type, "status": status, "at": now.isoformat(),
                   **detail}
            results.append(res)
            with self.factory() as s:
                s.add(AuditEvent(actor="scheduler", action=f"schedule.{job.type}",
                                 target=job.name, detail=res))
                s.commit()
        self.history.extend(results)
        return results

    def next_wake(self) -> datetime | None:
        due = [j.next_due for j in self.jobs if j.next_due is not None]
        return min(due) if due else None

    def run_forever(self, *, max_cycles: int | None = None,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        n = 0
        while max_cycles is None or n < max_cycles:
            self.run_cycle(clock())
            n += 1
            wake = self.next_wake()
            if wake is not None and (max_cycles is None or n < max_cycles):
                sleep(max(1.0, (wake - clock()).total_seconds()))

    # -- jobs
    def _run(self, job: Job, now: datetime) -> dict:
        if job.type == "update":
            return self._update(job, now)
        if job.type == "backup":
            return self._backup(job, now)
        return self._paper(job, now)

    def _backup(self, job: Job, now: datetime) -> dict:
        from apex.backup import backup

        keep = job.spec.get("keep")
        dest = backup(self.settings.database_url, Path(job.spec.get("folder", "backups")),
                      keep=None if keep is None else int(keep), now=now)
        return {"file": str(dest)}

    def _update(self, job: Job, now: datetime) -> dict:
        from apex.domain.instruments import by_symbol
        from apex.domain.market import Timeframe
        from apex.marketdata.ingest import update_bars

        sp = job.spec
        provider = self._provider(sp["provider"])
        tf = Timeframe(sp.get("timeframe", "1d"))
        start = datetime.fromisoformat(sp["backfill_start"]).replace(tzinfo=UTC)
        universe, stored, errors = by_symbol(), {}, []
        with self.factory() as s:
            for sym in sp["symbols"]:
                if sym not in universe:
                    errors.append(f"{sym}: unknown symbol")
                    continue
                r = update_bars(s, provider, universe[sym], tf, backfill_start=start, now=now)
                s.commit()
                if r is not None and r.status == "failed":
                    errors.append(f"{sym}: {r.error}")
                stored[sym] = 0 if r is None else r.stored
        return {"stored": stored, "errors": errors}

    def _paper(self, job: Job, now: datetime) -> dict:
        from apex.execution import store

        if self.settings.trading_mode is not TradingMode.PAPER:
            raise RuntimeError("paper jobs need APEX_TRADING_MODE=paper")
        out, errors = {}, []
        with self.factory() as s:
            for name in job.spec["accounts"]:
                try:
                    r = store.run_account(s, name, now=now)
                    out[name] = {"bars": r["bars"], "equity": round(r["equity"], 2),
                                 "kill_switch": r["kill_switch"]}
                except store.AccountHalted as exc:
                    errors.append(f"{name}: halted ({exc})")
                except KeyError:
                    errors.append(f"{name}: no such paper account")
        return {"accounts": out, "errors": errors}

    def _provider(self, name: str) -> object:
        if self.provider_factory is not None:
            return self.provider_factory(name)
        from apex.cli import make_provider

        return make_provider(name, self.settings)


def parse_jobs(cfg: dict) -> list[Job]:
    jobs, names = [], set()
    for i, j in enumerate(cfg.get("jobs", [])):
        kind = j.get("type")
        if kind not in KINDS:
            raise ValueError(f"job {i}: type must be one of {KINDS}")
        name = j.get("name") or f"{kind}-{i}"
        if name in names:
            raise ValueError(f"duplicate job name {name}")
        names.add(name)
        every = float(j.get("every_minutes", 60))
        if every < 1:
            raise ValueError(f"{name}: every_minutes must be >= 1")
        need = {"update": ("provider", "symbols", "backfill_start"), "paper": ("accounts",),
                "backup": ()}
        missing = [k for k in need[kind] if not j.get(k)]
        if missing:
            raise ValueError(f"{name}: missing {', '.join(missing)}")
        spec = {k: v for k, v in j.items() if k not in ("name", "type", "every_minutes")}
        jobs.append(Job(name, kind, timedelta(minutes=every), spec))
    if not jobs:
        raise ValueError("no jobs configured")
    return jobs
