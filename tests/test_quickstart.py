"""`apex quickstart`: empty database to a first simulated backtest, safe to repeat."""

from datetime import UTC, datetime

from sqlalchemy import func, select

from apex.cli import main as cli
from apex.core.config import Settings, get_settings
from apex.db.models import Experiment, PriceBar
from apex.db.session import make_engine
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.quickstart import run

NOW = datetime(2025, 6, 1, tzinfo=UTC)


def _count(url, model):
    engine = make_engine(url)
    try:
        with engine.connect() as c:
            return c.scalar(select(func.count()).select_from(model))
    finally:
        engine.dispose()


def test_fresh_folder_to_first_backtest_and_rerun_is_incremental(tmp_path):
    url = f"sqlite:///{tmp_path / 'apex.db'}"
    lines = []
    s = Settings(_env_file=None, database_url=url)
    assert run(s, symbol="BTCUSDT", out=lines.append, now=NOW,
               provider=SyntheticProvider()) == 0
    text = "\n".join(lines)
    assert "4/4" in text and "(simulated)" in text and "apex serve" in text
    bars = _count(url, PriceBar)
    assert bars > 1000 and _count(url, Experiment) == 1
    lines.clear()
    assert run(s, symbol="BTCUSDT", out=lines.append, now=NOW,
               provider=SyntheticProvider()) == 0
    assert "Up to date already." in "\n".join(lines) and _count(url, PriceBar) == bars


def test_unknown_market_and_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APEX_DATABASE_URL", f"sqlite:///{tmp_path / 'apex.db'}")
    get_settings.cache_clear()
    try:
        assert cli(["quickstart", "--symbol", "NOPE"]) == 2
        assert cli(["quickstart", "--provider", "synthetic", "--symbol", "SPY"]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "Unknown market NOPE" in out and "Backtest 1 (simulated)" in out
