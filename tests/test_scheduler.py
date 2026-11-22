"""Phase 3: in-process scheduler for data updates and paper accounts."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from apex.cli import main as cli
from apex.core.config import Settings
from apex.db.models import AuditEvent, PaperAccount, PriceBar
from apex.db.session import make_engine, make_session_factory
from apex.execution import store
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.scheduler import Scheduler, parse_jobs

T0 = datetime(2023, 1, 1, tzinfo=UTC)
JOBS = {"jobs": [
    {"name": "paper", "type": "paper", "accounts": ["acct", "missing"], "every_minutes": 1440},
    {"name": "data", "type": "update", "provider": "synthetic", "symbols": ["SPY", "NOPE"],
     "timeframe": "1d", "backfill_start": "2021-01-01", "every_minutes": 1440},
]}


def _sched(db_url, mode="paper"):
    engine = make_engine(db_url)
    settings = Settings(_env_file=None, database_url=db_url, trading_mode=mode)
    return Scheduler(parse_jobs(JOBS), settings, make_session_factory(engine),
                     provider_factory=lambda name: SyntheticProvider(annual_vol=0.4)), engine


def test_parse_jobs_validates():
    with pytest.raises(ValueError):
        parse_jobs({"jobs": []})
    with pytest.raises(ValueError):
        parse_jobs({"jobs": [{"type": "trade"}]})
    with pytest.raises(ValueError):
        parse_jobs({"jobs": [{"type": "update", "provider": "synthetic"}]})
    with pytest.raises(ValueError):
        parse_jobs({"jobs": [{"type": "paper", "accounts": ["a"], "every_minutes": 0}]})


def test_cycle_updates_data_then_runs_paper_and_is_idempotent(db_url, session):
    sched, engine = _sched(db_url)
    first = sched.run_cycle(T0 - timedelta(days=400))  # data first, nothing to trade yet
    assert [r["job"] for r in first] == ["data", "paper"]
    assert first[0]["status"] == "partial" and "NOPE: unknown symbol" in first[0]["errors"]
    store.create_account(session, "acct", strategy_key="TF-SMA-001", symbol="SPY",
                         timeframe="1d", source="synthetic", params={"fast": 10, "slow": 40})
    session.commit()
    assert sched.run_cycle(T0 - timedelta(days=399, hours=12)) == []  # nothing due yet
    res = sched.run_cycle(T0)
    data, paper = res
    assert data["stored"]["SPY"] > 200
    assert paper["accounts"]["acct"]["bars"] > 300 and "missing: no such" in paper["errors"][0]
    n_bars = session.scalar(select(func.count()).select_from(PriceBar))
    again = sched.run_cycle(T0 + timedelta(days=1, seconds=1))  # restart-safe: no dupes
    assert again[0]["stored"]["SPY"] <= 1 and again[1]["accounts"]["acct"]["bars"] <= 1
    assert session.scalar(select(func.count()).select_from(PriceBar)) <= n_bars + 1
    acts = session.scalars(select(AuditEvent.action).where(AuditEvent.actor == "scheduler"))
    assert {"schedule.update", "schedule.paper"} <= set(acts)
    engine.dispose()


def test_paper_jobs_refuse_research_mode_and_failures_do_not_stop_others(db_url, session):
    sched, engine = _sched(db_url, mode="research")
    res = {r["job"]: r for r in sched.run_cycle(T0)}
    assert res["paper"]["status"] == "failed" and "APEX_TRADING_MODE" in res["paper"]["error"]
    assert res["data"]["stored"]["SPY"] > 0  # the data job still ran
    assert session.scalar(select(func.count()).select_from(PaperAccount)) == 0
    engine.dispose()


def test_run_forever_sleeps_until_next_job(db_url):
    sched, engine = _sched(db_url, mode="research")
    t = [T0]
    slept = []

    def sleep(sec):
        slept.append(sec)
        t[0] += timedelta(seconds=sec)

    sched.run_forever(max_cycles=2, sleep=sleep, clock=lambda: t[0])
    assert slept == [86400.0] and len(sched.history) == 4
    engine.dispose()


def test_cli_schedule_once(db_url, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    from apex.core.config import get_settings
    get_settings.cache_clear()
    cfg = tmp_path / "jobs.json"
    cfg.write_text(json.dumps({"jobs": [{"type": "update", "provider": "synthetic",
                                         "symbols": ["SPY"], "timeframe": "1d",
                                         "backfill_start": "2024-01-01"}]}))
    try:
        assert cli(["schedule", "--config", str(cfg), "--once"]) == 0
    finally:
        get_settings.cache_clear()
    assert '"status": "ok"' in capsys.readouterr().out


def test_backup_job_runs_last_and_postgres_fails_softly(tmp_path):
    import sqlite3

    with sqlite3.connect(tmp_path / "apex.db") as c:
        c.execute("CREATE TABLE t (x)")
    jobs = parse_jobs({"jobs": [
        {"name": "bk", "type": "backup", "folder": str(tmp_path / "b"), "keep": 2},
        {"name": "data", "type": "update", "provider": "synthetic", "symbols": ["SPY"],
         "backfill_start": "2021-01-01"},
    ]})
    sched = Scheduler(jobs, Settings(_env_file=None,
                                     database_url=f"sqlite:///{tmp_path / 'apex.db'}"), None)
    assert [j.name for j in sched.due(T0)] == ["data", "bk"]
    assert sched._backup(jobs[0], T0)["file"].endswith("apex-backup-20230101-000000.db")
    pg = Scheduler(jobs, Settings(_env_file=None, database_url="postgresql+psycopg://x/y"), None)
    with pytest.raises(Exception, match="pg_dump"):
        pg._backup(jobs[0], T0)


def test_dashboard_shows_each_jobs_latest_run_and_flags_late_ones(db_url, session):
    from fastapi.testclient import TestClient

    from apex.api.app import create_app

    sched, engine = _sched(db_url, mode="research")
    sched.run_cycle(T0)
    # an older run of the data job, and a backup job last seen two days ago (interval 1 day)
    session.add(AuditEvent(actor="scheduler", action="schedule.update", target="data",
                           at=datetime.now(UTC) - timedelta(days=5),
                           detail={"status": "failed", "error": "old"}))
    session.add(AuditEvent(actor="scheduler", action="schedule.backup", target="nightly",
                           at=datetime.now(UTC) - timedelta(days=2, hours=1),
                           detail={"status": "ok", "every_minutes": 1440}))
    session.add(AuditEvent(actor="operator", action="schedule.update", target="ignored"))
    session.commit()
    c = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                              make_session_factory(engine)))
    jobs = {j["job"]: j for j in c.get("/scheduler/jobs").json()}
    assert set(jobs) == {"data", "paper", "nightly"}
    assert jobs["data"]["status"] == "partial" and not jobs["data"]["late"]  # the newest run
    assert jobs["data"]["every_minutes"] == 1440 and "NOPE: unknown symbol" in jobs["data"]["errors"]
    assert jobs["paper"]["status"] == "failed" and "APEX_TRADING_MODE" in jobs["paper"]["errors"][0]
    assert jobs["nightly"]["late"] and jobs["nightly"]["type"] == "backup"
    page = c.get("/ui/").text
    assert 'id="sched"' in page and '"/scheduler/jobs"' in page
    engine.dispose()
