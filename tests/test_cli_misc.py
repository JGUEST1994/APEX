"""CLI paths not covered elsewhere: provider factory, db-upgrade, hypotheses, research
import/search, and refusing unknown symbols."""

import pytest

from apex.cli import main as cli
from apex.cli import make_provider
from apex.core.clock import FixedClock, SystemClock
from apex.core.config import Settings, get_settings
from tests.conftest import utc


@pytest.fixture
def env(db_url, monkeypatch):
    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_provider_factory_builds_keyless_sources_and_refuses_missing_keys(tmp_path):
    s = Settings(_env_file=None)
    names = {n: make_provider(n, s, str(tmp_path)).name
             for n in ("synthetic", "csv", "binance", "kraken", "coinbase")}
    assert names == {"synthetic": "synthetic", "csv": "csv", "binance": "binance",
                     "kraken": "kraken", "coinbase": "coinbase"}
    with pytest.raises(SystemExit, match="APEX_ALPACA_KEY_ID"):
        make_provider("alpaca", s)
    with pytest.raises(SystemExit, match="APEX_POLYGON_API_KEY"):
        make_provider("polygon", s)
    with pytest.raises(SystemExit, match="unknown provider"):
        make_provider("nope", s)


def test_db_upgrade_hypotheses_and_unknown_symbol(env, capsys):
    assert cli(["db-upgrade"]) == 0
    assert cli(["db-upgrade"]) == 0  # idempotent
    assert cli(["hypotheses"]) == 0
    assert cli(["ingest", "--provider", "synthetic", "--symbol", "NOPE", "--start",
                "2024-01-01", "--end", "2024-02-01"]) == 2
    out = capsys.readouterr()
    assert "database at head" in out.out and "| H01 |" in out.out
    assert "unknown symbol NOPE" in out.err


def test_research_import_and_search_from_the_cli(env, tmp_path, capsys):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "vwap.md").write_text("# VWAP notes\nVWAP mean reversion fades stretches from "
                                   "the volume weighted average price.")
    (notes / "orb.txt").write_text("Opening range breakout: buy above the first 30 minute high.")
    (notes / "skip.xyz").write_text("not a supported format")
    (notes / "empty.txt").write_text("   ")
    assert cli(["research-import", str(notes), "--licence-note", "own notes"]) == 0
    assert cli(["research-search", "opening range breakout"]) == 0
    out = capsys.readouterr()
    assert out.out.count("document ") == 2 and "skip.xyz" not in out.out + out.err
    assert "skipped empty.txt" in out.err
    assert "Opening range breakout" in out.out.splitlines()[-1]


def test_clocks():
    c = FixedClock(utc(2025, 1, 1))
    c.advance(hours=2)
    assert c.now() == utc(2025, 1, 1, 2)
    assert SystemClock().now().tzinfo is not None
    with pytest.raises(ValueError):
        from datetime import datetime
        FixedClock(datetime(2025, 1, 1))
