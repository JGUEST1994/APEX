import math

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from apex.api.app import create_app
from apex.backtest.engine import BacktestConfig, fill_cost, run_backtest
from apex.backtest.experiments import run_experiment
from apex.backtest.metrics import max_consecutive, max_drawdown, periods_per_year
from apex.cli import main as cli
from apex.core.config import Settings
from apex.db.models import Experiment, StrategyVersion
from apex.db.session import make_engine, make_session_factory
from apex.domain.instruments import by_symbol
from apex.domain.market import AssetClass, CostModel, InstrumentSpec, Timeframe
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.strategies.base import Strategy, StrategyMeta
from apex.strategies.registry import all_strategies
from tests.conftest import utc

FREE = InstrumentSpec("TEST", AssetClass.CRYPTO, "X", "crypto_24x7", "USD", lot_size=1.0)


class Fixed(Strategy):
    """Plays back a fixed position sequence (test double)."""

    meta = StrategyMeta(
        key="TEST-FIXED", version="0.0.1", name="fixed", category="test", description="",
        entry_rules="", exit_rules="", stop_loss="", take_profit="", position_sizing="",
        asset_classes=(AssetClass.CRYPTO,), timeframes=(Timeframe.D1,),
        default_params={"seq": []})

    def target_position(self, bars, params):
        return pd.Series(params["seq"][: len(bars)], index=bars.index, dtype=float)


def _bars(opens, closes=None):
    closes = closes or opens
    n = len(opens)
    return pd.DataFrame({"ts": pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC"),
                         "open": opens, "high": [max(o, c) for o, c in zip(opens, closes, strict=True)],
                         "low": [min(o, c) for o, c in zip(opens, closes, strict=True)], "close": closes,
                         "volume": [1.0] * n})


def test_hand_calculated_long_trade_no_costs():
    # signal long on bar 0 -> fill at bar 1 open (10); exit signal bar 2 -> fill bar 3 open (12)
    bars = _bars([10, 10, 11, 12, 12])
    cfg = BacktestConfig(initial_capital=100.0)
    r = run_backtest(bars, Fixed(), FREE, {"seq": [1, 1, 0, 0, 0]}, cfg)
    assert list(r.fills["price"]) == [10, 12]
    assert list(r.fills["units"]) == [10, -10]
    assert list(r.equity) == [100, 100, 110, 120, 120]
    assert len(r.trades) == 1
    t = r.trades[0]
    assert (t.side, t.entry_price, t.exit_price, t.pnl, t.bars_held) == (1, 10, 12, 20, 2)
    assert r.metrics["total_return"] == pytest.approx(0.2)
    assert r.metrics["win_rate"] == 1.0


def test_short_and_flip_with_costs():
    spec = InstrumentSpec("TEST", AssetClass.CRYPTO, "X", "crypto_24x7", "USD", lot_size=1.0,
                          costs=CostModel(commission_bps=10.0))  # 0.1% per fill
    bars = _bars([10, 10, 8, 8, 10, 10])
    r = run_backtest(bars, Fixed(), spec, {"seq": [-1, -1, 1, 1, 1, 1]},
                     BacktestConfig(initial_capital=100.0))
    # bar1: short 10 @10, cost 0.1 -> cash 199.9; bar3 open 8: equity 119.9 -> flip to +14 units
    f = r.fills
    assert list(f["units"]) == [-10, 24]
    assert f["cost"].iloc[0] == pytest.approx(0.1)
    assert f["cost"].iloc[1] == pytest.approx(24 * 8 * 0.001)
    short, long_ = r.trades
    assert short.side == -1 and short.pnl == pytest.approx(20 - 0.1 - 10 * 8 * 0.001)
    assert long_.side == 1 and long_.closed_at_end
    assert r.total_costs == pytest.approx(f["cost"].sum())
    assert r.equity.iloc[-1] == pytest.approx(100 + short.pnl + long_.pnl)


def test_no_same_bar_fill_and_latency():
    bars = _bars([10, 20, 30, 40])
    r = run_backtest(bars, Fixed(), FREE, {"seq": [1, 1, 1, 1]},
                     BacktestConfig(initial_capital=100.0, latency_bars=2))
    assert r.fills["ts"].iloc[0] == bars["ts"].iloc[2]
    with pytest.raises(ValueError):
        BacktestConfig(latency_bars=0)


def test_futures_multiplier_and_lot_rounding():
    es = by_symbol()["ES"]
    bars = _bars([5000, 5000, 5100])
    spec = InstrumentSpec("ES", AssetClass.FUTURES, "CME", "cme_globex", "USD",
                          tick_size=0.25, contract_multiplier=50.0)
    r = run_backtest(bars, Fixed(), spec, {"seq": [1, 1, 1]},
                     BacktestConfig(initial_capital=600_000.0))
    assert r.fills["units"].iloc[0] == 2  # floor(600k / 250k)
    assert r.equity.iloc[-1] == pytest.approx(600_000 + 2 * 100 * 50)
    assert fill_cost(es, 2, 5000) > 0


def test_cost_multiplier_reduces_returns():
    bars = SyntheticProvider().fetch_bars(by_symbol()["BTCUSDT"], Timeframe.H1,
                                          utc(2024, 1, 1), utc(2024, 6, 1))
    strat = all_strategies()["TF-SMA-001"]()
    a = run_backtest(bars, strat, by_symbol()["BTCUSDT"])
    b = run_backtest(bars, strat, by_symbol()["BTCUSDT"], config=BacktestConfig(cost_multiplier=3))
    assert b.total_costs > a.total_costs and b.equity.iloc[-1] < a.equity.iloc[-1]


def test_deterministic():
    bars = SyntheticProvider().fetch_bars(by_symbol()["SPY"], Timeframe.D1, utc(2020, 1, 1), utc(2025, 1, 1))
    strat = all_strategies()["TF-SMA-001"]()
    assert run_backtest(bars, strat, by_symbol()["SPY"]).metrics == run_backtest(bars, strat, by_symbol()["SPY"]).metrics


def test_metric_helpers():
    eq = pd.Series([100, 120, 90, 130], index=pd.date_range("2025", periods=4, freq="D", tz="UTC"))
    assert max_drawdown(eq) == pytest.approx(-0.25)
    assert max_consecutive([True, True, False, True]) == 2
    assert periods_per_year(eq.index) == pytest.approx(365.25)


def test_metrics_shape():
    bars = _bars([10, 11, 10, 12, 11, 13, 12, 14])
    r = run_backtest(bars, Fixed(), FREE, {"seq": [1, 0, 1, 0, 1, 0, 1, 0]},
                     BacktestConfig(initial_capital=1000.0))
    m = r.metrics
    for k in ["total_return", "cagr", "sharpe", "sortino", "max_drawdown", "calmar", "win_rate",
              "profit_factor", "expectancy", "avg_win", "avg_loss", "max_consecutive_losses",
              "trades_per_year", "exposure", "total_costs", "pct_positive_months"]:
        assert k in m
    assert all(v is None or not isinstance(v, float) or math.isfinite(v) for v in m.values())


def test_experiment_recorded_and_reproducible(session):
    spy = by_symbol()["SPY"]
    ingest_bars(session, SyntheticProvider(), spy, Timeframe.D1, utc(2021, 1, 1), utc(2025, 1, 1))
    session.commit()
    strat = all_strategies()["TF-SMA-001"]()
    kw = dict(symbol="SPY", timeframe=Timeframe.D1, source="synthetic",
              start=utc(2021, 1, 1), end=utc(2025, 1, 1), params={"fast": 10, "slow": 40})
    e1, _ = run_experiment(session, strat, **kw)
    e2, _ = run_experiment(session, strat, **kw)
    session.commit()
    assert e1.metrics == e2.metrics
    assert e1.data_manifest["data_sha256"] == e2.data_manifest["data_sha256"]
    assert e1.params == {"fast": 10, "slow": 40, "allow_short": True}
    assert e1.code_version
    ver = session.get(StrategyVersion, e1.strategy_version_id)
    assert ver.status == "experimental" and ver.code_sha256


def test_cli_and_api_backtest(db_url, monkeypatch, capsys):
    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    from apex.core.config import get_settings
    get_settings.cache_clear()
    try:
        assert cli(["ingest", "--provider", "synthetic", "--symbol", "ES", "--timeframe", "1h",
                    "--start", "2025-01-01", "--end", "2025-03-01"]) == 0
        assert cli(["backtest", "--strategy", "TF-SMA-001", "--symbol", "ES", "--timeframe", "1h",
                    "--source", "synthetic", "--start", "2025-01-01", "--end", "2025-03-01",
                    "--param", "fast=5", "--param", "slow=30"]) == 0
        assert cli(["backtest", "--strategy", "NOPE", "--symbol", "ES", "--source", "synthetic",
                    "--start", "2025-01-01", "--end", "2025-03-01"]) == 2
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "SIMULATED" in out and "sharpe" in out
    engine = make_engine(db_url)
    client = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                                   make_session_factory(engine)))
    exps = client.get("/experiments").json()
    assert exps[0]["simulated"] is True and exps[0]["params"]["fast"] == 5
    engine.dispose()
    assert Experiment  # imported for clarity
