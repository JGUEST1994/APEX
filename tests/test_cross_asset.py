"""H05 cross-asset trend portfolio (TF-XASSET-001): any number of legs, inverse-vol sizing."""

import numpy as np
import pandas as pd
import pytest

from apex.backtest.engine import BacktestConfig
from apex.backtest.portfolio import run_portfolio_backtest
from apex.domain.instruments import by_symbol
from apex.strategies.multi import CrossAssetTrend, PairsZScore, all_multi_strategies

SYMS = by_symbol()
LEGS = ("SPY", "BTCUSDT", "ETHUSDT")


def _bars(logp):
    close = np.exp(np.asarray(logp, float))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"ts": pd.date_range("2020-01-01", periods=len(close), freq="1D",
                                             tz="UTC"),
                         "open": open_, "high": np.maximum(open_, close),
                         "low": np.minimum(open_, close), "close": close, "volume": 1.0})


def _market(seed=0, n=900, drifts=(0.0, 0.0, 0.0), vols=(0.01, 0.02, 0.04)):
    rng = np.random.default_rng(seed)
    return {s: _bars(np.log(100) + np.cumsum(rng.normal(d, v, n)))
            for s, d, v in zip(LEGS, drifts, vols, strict=True)}


def test_registered_and_takes_any_number_of_legs():
    assert all_multi_strategies()["TF-XASSET-001"] is CrossAssetTrend
    specs = {s: SYMS[s] for s in LEGS}
    for k in (2, 3):
        bars = dict(list(_market().items())[:k])
        run_portfolio_backtest(bars, CrossAssetTrend(), {s: specs[s] for s in bars})
    with pytest.raises(ValueError, match="2 or more"):
        run_portfolio_backtest({"SPY": _market()["SPY"]}, CrossAssetTrend(), specs)
    with pytest.raises(ValueError, match="needs 2"):
        run_portfolio_backtest(_market(), PairsZScore(), specs)


def test_weights_are_causal_inverse_vol_and_only_change_on_rebalance():
    s = CrossAssetTrend()
    p = s.params()
    bars = _market()
    full = s.target_weights(bars, p)
    part = s.target_weights({k: v.iloc[:500].copy() for k, v in bars.items()}, p)
    pd.testing.assert_frame_equal(full.iloc[:500], part)
    changes = [t for t in range(1, len(full)) if not full.iloc[t].equals(full.iloc[t - 1])]
    assert changes and all(t % p["rebalance"] == 0 for t in changes)
    live = full[(full != 0).all(axis=1)]
    assert len(live) > 300
    assert np.allclose(live.abs().sum(axis=1), p["gross"])
    # quiet market gets the biggest weight, wild one the smallest (vols 1% : 2% : 4%)
    a = live.abs().mean()
    assert a["SPY"] > a["BTCUSDT"] > a["ETHUSDT"]
    assert a["SPY"] / a["ETHUSDT"] == pytest.approx(4, rel=0.25)


def test_long_only_never_shorts():
    s = CrossAssetTrend()
    w = s.target_weights(_market(drifts=(-0.002, 0.0, 0.002)), s.params({"long_only": True}))
    assert (w >= 0).all().all() and (w["ETHUSDT"] > 0).any()


def test_profits_from_trends_in_both_directions():
    specs = {s: SYMS[s] for s in LEGS}
    cheap = BacktestConfig(cost_multiplier=0.2)
    trend = [run_portfolio_backtest(_market(seed, drifts=(0.001, -0.002, 0.004)),
                                    CrossAssetTrend(), specs, config=cheap).metrics
             for seed in range(5)]
    assert all(m["total_return"] > 0.1 for m in trend)
    assert np.mean([m["sharpe"] for m in trend]) > 1.2
    flat = [run_portfolio_backtest(_market(seed), CrossAssetTrend(), specs,
                                   config=cheap).metrics["sharpe"] for seed in range(5)]
    assert np.mean(flat) < 0.5  # driftless random walks: no real edge


def test_cli_backtests_three_legs(db_url, monkeypatch, capsys):
    from apex.cli import main as cli
    from apex.core.config import get_settings

    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        for sym in LEGS:
            assert cli(["ingest", "--provider", "synthetic", "--symbol", sym, "--timeframe",
                        "1d", "--start", "2021-01-01", "--end", "2024-01-01"]) == 0
        assert cli(["backtest-pair", "--strategy", "TF-XASSET-001", "--symbols",
                    ",".join(LEGS), "--source", "synthetic", "--start", "2021-01-01",
                    "--end", "2024-01-01"]) == 0
        assert cli(["strategies"]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "(SIMULATED) SPY/BTCUSDT/ETHUSDT" in out
    from fastapi.testclient import TestClient

    from apex.api.app import create_app
    from apex.core.config import Settings
    from apex.db.session import make_engine, make_session_factory

    engine = make_engine(db_url)
    c = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                              make_session_factory(engine)))
    exp = c.get("/experiments").json()[0]
    assert exp["strategy"] == "TF-XASSET-001" and exp["data_manifest"]["symbols"] == list(LEGS)
    st = {r["key"]: r["instruments"] for r in c.get("/strategies").json()}
    assert st["TF-XASSET-001"] == "2+" and st["STAT-PAIR-001"] == 2 and st["TF-SMA-001"] == 1
    engine.dispose()
    assert "TF-XASSET-001" in out and "[2+ instruments]" in out


def test_walk_forward_on_three_legs():
    from apex.backtest.validation import walk_forward

    bars = _market(n=1000, drifts=(0.001, -0.002, 0.004))
    grid = {"lookback": [60, 120], "rebalance": [10, 21]}
    wf = walk_forward(bars, CrossAssetTrend(), {s: SYMS[s] for s in LEGS}, grid,
                      train_bars=400, test_bars=200)
    assert len(wf.folds) == 3 and wf.n_trials == 4 and len(wf.oos_returns) == 600
    assert wf.summary["mean_oos_sharpe"] > 0.2  # planted trends survive out of sample
