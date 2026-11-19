import numpy as np
import pandas as pd
import pytest

from apex.backtest.validation import (
    deflated_sharpe, expected_max_sharpe, monte_carlo_trades, param_grid,
    probabilistic_sharpe, probability_of_backtest_overfitting, returns_matrix, sensitivity,
    split_holdout, walk_forward,
)
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.strategies.registry import all_strategies
from tests.conftest import utc

SPY = by_symbol()["SPY"]


@pytest.fixture(scope="module")
def daily():
    return SyntheticProvider().fetch_bars(SPY, Timeframe.D1, utc(2015, 1, 1), utc(2025, 1, 1))


def test_psr_basics():
    assert probabilistic_sharpe(0.0, 250) == pytest.approx(0.5)
    assert probabilistic_sharpe(0.1, 250) > 0.9
    assert probabilistic_sharpe(0.1, 250, skew=-2, kurtosis=10) < probabilistic_sharpe(0.1, 250)


def test_expected_max_sharpe_grows_with_trials():
    assert expected_max_sharpe(1, 0.01) == 0
    assert 0 < expected_max_sharpe(10, 0.01) < expected_max_sharpe(1000, 0.01)


def test_deflated_sharpe_penalises_trials():
    rng = np.random.default_rng(1)
    r = pd.Series(rng.normal(0.001, 0.01, 1000))
    one = deflated_sharpe(r, 1, 0.0005)
    many = deflated_sharpe(r, 500, 0.0005)
    assert one["dsr"] == pytest.approx(one["psr_vs_zero"])
    assert many["dsr"] < one["dsr"]


def test_pbo_noise_vs_real_edge():
    rng = np.random.default_rng(3)
    noise = pd.DataFrame(rng.normal(0, 0.01, (800, 20)))
    pbo_noise = probability_of_backtest_overfitting(noise)["pbo"]
    assert 0.2 < pbo_noise < 0.9
    edge = noise.copy()
    edge[0] += 0.004  # one configuration with a persistent edge
    assert probability_of_backtest_overfitting(edge)["pbo"] < 0.1
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting(noise[[0]])
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting(noise, n_splits=5)


def test_monte_carlo():
    pnl = [100, -50, 80, -120, 60, 40, -30] * 10
    a = monte_carlo_trades(pnl, 10_000, n=500, seed=1)
    b = monte_carlo_trades(pnl, 10_000, n=500, seed=1)
    assert a == b
    p = a["max_drawdown_pct"]
    assert p[5] <= p[50] <= p[95] <= 0
    assert monte_carlo_trades([], 1)["n"] == 0


def test_param_grid():
    assert param_grid({"a": [1, 2], "b": [3]}) == [{"a": 1, "b": 3}, {"a": 2, "b": 3}]


def test_walk_forward(daily):
    strat = all_strategies()["TF-SMA-001"]()
    wf = walk_forward(daily, strat, SPY, {"fast": [10, 20], "slow": [50, 100]},
                      train_bars=750, test_bars=250)
    assert len(wf.folds) == (len(daily) - 750) // 250
    for a, b in zip(wf.folds, wf.folds[1:], strict=False):
        assert a.test_end < b.test_start  # non-overlapping OOS windows
    for f in wf.folds:
        assert f.params["fast"] in (10, 20) and f.params["slow"] in (50, 100)
        assert f.train_start < f.test_start
    # OOS returns only cover test windows
    assert wf.oos_returns.index.min() == wf.folds[0].test_start
    s = wf.summary
    assert s["n_trials"] == 4 * len(wf.folds)
    assert 0 <= s["deflated_sharpe"] <= 1 and 0 <= s["pct_profitable_folds"] <= 1
    again = walk_forward(daily, strat, SPY, {"fast": [10, 20], "slow": [50, 100]},
                         train_bars=750, test_bars=250)
    assert again.summary == s


def test_walk_forward_too_short(daily):
    with pytest.raises(ValueError):
        walk_forward(daily.iloc[:100], all_strategies()["TF-SMA-001"](), SPY, {"fast": [10]},
                     train_bars=80, test_bars=50)


def test_sensitivity_and_returns_matrix(daily):
    strat = all_strategies()["TF-SMA-001"]()
    df = sensitivity(daily, strat, SPY, {}, "fast", [5, 10, 60])
    assert list(df["fast"]) == [5, 10, 60] and df["error"].notna().iloc[-1]  # fast >= slow
    m = returns_matrix(daily, strat, SPY, {"fast": [10, 20], "slow": [50, 100]})
    assert m.shape == (len(daily), 4)


def test_holdout(daily):
    research, hold = split_holdout(daily, 0.2)
    assert len(research) + len(hold) == len(daily)
    assert research["ts"].max() < hold["ts"].min()
    with pytest.raises(ValueError):
        split_holdout(daily, 1.5)


def test_cli_validate(db_url, monkeypatch, capsys):
    from apex.cli import main as cli
    from apex.core.config import get_settings

    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        assert cli(["ingest", "--provider", "synthetic", "--symbol", "SPY", "--timeframe", "1d",
                    "--start", "2016-01-01", "--end", "2025-01-01"]) == 0
        assert cli(["validate", "--strategy", "TF-SMA-001", "--symbol", "SPY",
                    "--source", "synthetic", "--start", "2016-01-01", "--end", "2025-01-01",
                    "--grid", "fast=10,20", "--grid", "slow=50,100",
                    "--train-bars", "500", "--test-bars", "250"]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "deflated_sharpe" in out and "pbo" in out and "out-of-sample" in out
