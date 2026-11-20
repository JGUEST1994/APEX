"""`apex export`: CSV files of results, prices and practice fills."""

import pandas as pd
import pytest

from apex.cli import main as cli
from apex.core.config import get_settings


@pytest.fixture
def env(db_url, monkeypatch):
    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_export_bars_experiments_and_leaderboard(env, tmp_path, capsys):
    assert cli(["seed-instruments"]) == 0
    assert cli(["ingest", "--provider", "synthetic", "--symbol", "SPY", "--timeframe", "1d",
                "--start", "2024-01-01", "--end", "2024-04-01"]) == 0
    assert cli(["backtest", "--strategy", "TF-SMA-001", "--symbol", "SPY", "--timeframe", "1d",
                "--source", "synthetic", "--start", "2024-01-01", "--end", "2024-04-01",
                "--param", "fast=5", "--param", "slow=20"]) == 0
    b, e, lb = tmp_path / "b.csv", tmp_path / "out" / "e.csv", tmp_path / "lb.csv"
    assert cli(["export", "bars", "--out", str(b), "--symbol", "SPY", "--timeframe", "1d",
                "--source", "synthetic"]) == 0
    bars = pd.read_csv(b)
    assert list(bars.columns[:5]) == ["ts", "open", "high", "low", "close"] and len(bars) > 50
    assert bars["ts"].iloc[0].startswith("2024-01-0") and "+00:00" in bars["ts"].iloc[0]
    assert cli(["export", "experiments", "--out", str(e)]) == 0
    ex = pd.read_csv(e)
    assert ex.loc[0, "strategy"] == "TF-SMA-001" and ex.loc[0, "symbol"] == "SPY"
    assert bool(ex.loc[0, "simulated"]) and "m_sharpe" in ex.columns
    assert '"fast": 5' in ex.loc[0, "params"]
    assert cli(["export", "leaderboard", "--out", str(lb)]) == 0  # empty is fine
    assert cli(["export", "experiments", "--out", str(e)]) == 2  # no silent overwrite
    assert cli(["export", "experiments", "--out", str(e), "--force"]) == 0
    err = capsys.readouterr().err
    assert "already exists" in err


def test_export_argument_errors(env, tmp_path, capsys):
    out = str(tmp_path / "x.csv")
    assert cli(["export", "bars", "--out", out]) == 2
    assert cli(["export", "bars", "--out", out, "--symbol", "NOPE", "--timeframe", "1d",
                "--source", "synthetic"]) == 2
    assert cli(["export", "fills", "--out", out]) == 2
    assert cli(["export", "fills", "--out", out, "--name", "ghost"]) == 2
    err = capsys.readouterr().err
    assert "needs --symbol" in err and "needs --name" in err and "ghost" in err


def test_export_fills_from_a_practice_account(session):
    from apex import export
    from apex.db.models import PaperAccount, PaperFill
    from tests.conftest import utc

    a = PaperAccount(name="p1", strategy_key="TF-SMA-001", symbol="SPY", timeframe="1d",
                     source="synthetic", initial_capital=1e5, cash=1e5, peak_equity=1e5,
                     day_start_equity=1e5)
    session.add(a)
    session.flush()
    session.add(PaperFill(account_id=a.id, client_order_id="p1-1", symbol="SPY", qty=10,
                          price=100.0, cost=0.5, ts=utc(2024, 1, 2)))
    session.commit()
    df = export.fills(session, "p1")
    assert df.to_dict("records") == [{"ts": "2024-01-02T00:00:00+00:00", "symbol": "SPY",
                                      "qty": 10.0, "price": 100.0, "cost": 0.5,
                                      "client_order_id": "p1-1", "simulated": True}]
    assert export.fills(session, "p1").columns.tolist()[-1] == "simulated"
