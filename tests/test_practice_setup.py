"""The practice-run steps (USER_GUIDE.md for Windows, MAC_SETUP.md section 9 for a Mac) and
their jobs file work as written."""

import json
import shlex
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apex.cli import main as cli
from apex.db.models import PaperAccount
from apex.domain.instruments import by_symbol
from apex.scheduler import parse_jobs

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["USER_GUIDE.md", "MAC_SETUP.md"]


def _command(doc, name):
    """The documented ``apex <name> ...`` line for the fund basket, as arguments."""
    text = (ROOT / "docs" / doc).read_text()
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip().startswith(f"apex {name} ") and "XLY" in ln]
    assert len(lines) == 1, lines
    return shlex.split(lines[0])[1:]


def _swap(args, flag, value):
    i = args.index(flag)
    return [*args[:i + 1], value, *args[i + 2:]]


@pytest.mark.parametrize("doc", DOCS)
def test_the_practice_jobs_file_matches_the_documented_account(doc):
    cfg = json.loads((ROOT / "jobs.practice.example.json").read_text())
    jobs = {j.type: j for j in parse_jobs(cfg)}
    assert set(jobs) == {"update", "paper", "backup"}
    create = _command(doc, "paper-create")
    symbols = create[create.index("--symbol") + 1].split(",")
    assert len(symbols) == 13 and set(symbols) <= set(by_symbol())
    assert jobs["update"].spec["symbols"] == symbols
    assert jobs["update"].spec["provider"] == create[create.index("--source") + 1] == "tiingo"
    update = _command(doc, "update")
    assert update[update.index("--symbols") + 1].split(",") == symbols
    assert _command(DOCS[0], "paper-create") == _command(DOCS[1], "paper-create")
    assert jobs["paper"].spec["accounts"] == [create[create.index("--name") + 1]]


@pytest.mark.parametrize("doc", DOCS)
def test_the_documented_commands_create_the_practice_account(doc, db_url, monkeypatch, capsys):
    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    monkeypatch.setenv("APEX_TRADING_MODE", "paper")
    from apex.core.config import get_settings

    get_settings.cache_clear()
    try:  # the same commands, on synthetic prices instead of Tiingo's
        update = _swap(_swap(_command(doc, "update"), "--provider", "synthetic"),
                       "--backfill-start", "2025-06-01")
        assert cli(update) == 0
        create = _swap(_swap(_command(doc, "paper-create"), "--source", "synthetic"),
                       "--by", "Sam")
        assert cli(create) == 0
        assert cli(["paper-run", "--name", "fund-basket"]) == 0  # nothing new yet
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "created paper account fund-basket (SIMULATED), trading prices after" in out
    assert ("safety limits for this account only: max_positions 13 (standard 10); set by Sam, "
            "recorded in the audit log") in out
    with Session(create_engine(db_url)) as s:
        acc = s.scalar(select(PaperAccount).where(PaperAccount.name == "fund-basket"))
        assert acc.strategy_key == "MR-IBS-BASKET-001" and acc.params == {"gross": 0.38}
        assert acc.limits["max_positions"] == 13 and acc.limits["max_cluster_exposure"] == 0.4
        assert acc.starts_after is not None and len(acc.symbols) == 13
