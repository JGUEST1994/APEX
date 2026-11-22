"""Keeping downloaded prices between research runs (apex/price_cache.py)."""

import sqlite3

import pytest

from apex.cli import main as cli
from apex.core.config import get_settings

INGEST = ["ingest", "--provider", "synthetic", "--symbol", "SPY", "--timeframe", "1d",
          "--start", "2016-01-01", "--end", "2020-01-01"]


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    path = tmp_path / "research.db"
    monkeypatch.setenv("APEX_DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    assert cli(["db-upgrade"]) == 0
    yield path
    get_settings.cache_clear()


def _count(path, table):
    with sqlite3.connect(path) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def test_reuse_skips_a_download_already_stored(sqlite_db, capsys):
    assert cli(INGEST + ["--reuse"]) == 0  # nothing stored yet: downloads
    assert ": ok " in capsys.readouterr().out
    assert cli(INGEST + ["--reuse"]) == 0
    assert "reused" in capsys.readouterr().out
    assert _count(sqlite_db, "ingestion_runs") == 1
    # a different request (another end date) is fetched, never guessed from the stored one
    assert cli(INGEST[:-1] + ["2021-01-01", "--reuse"]) == 0
    assert "reused" not in capsys.readouterr().out
    assert _count(sqlite_db, "ingestion_runs") == 2
    assert cli(INGEST) == 0  # without --reuse it always downloads
    assert _count(sqlite_db, "ingestion_runs") == 3


def test_the_saved_copy_keeps_prices_but_no_research_results(sqlite_db, tmp_path, capsys):
    assert cli(INGEST) == 0
    assert cli(["tournament", "--strategies", "TF-SMA-001", "--no-promote", "--source",
                "synthetic", "--timeframe", "1d", "--symbols", "SPY", "--start", "2016-01-01",
                "--end", "2020-01-01", "--train-bars", "300", "--test-bars", "150"]) == 0
    assert _count(sqlite_db, "experiments") > 0
    bars = _count(sqlite_db, "bars")
    out = tmp_path / "cache" / "prices.db"
    assert cli(["price-cache-save", "--out", str(out)]) == 0
    assert f"saved {bars} bars" in capsys.readouterr().out
    assert _count(out, "bars") == bars and _count(out, "ingestion_runs") == 1
    for t in ("experiments", "strategy_versions", "strategies", "audit_log"):
        assert _count(out, t) == 0
    assert _count(sqlite_db, "experiments") > 0  # the run's own database is untouched


def test_price_cache_needs_a_sqlite_file(tmp_path):
    from apex.price_cache import save_prices_only

    with pytest.raises(ValueError, match="SQLite"):
        save_prices_only("postgresql+psycopg://x:y@localhost/none", tmp_path / "p.db")
