"""Walk-forward, PBO, tournament and leaderboard for multi-instrument strategies."""

import numpy as np
import pandas as pd
import pytest

from apex.backtest.experiments import run_validation
from apex.backtest.tournament import leaderboard, run_tournament
from apex.backtest.validation import _segment, returns_matrix, walk_forward
from apex.cli import main as cli
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.strategies.multi import PairsZScore, all_multi_strategies
from apex.strategies.registry import all_strategies
from tests.conftest import utc

SYMS = by_symbol()
GRID = {"lookback": [40, 60], "entry_z": [1.5, 2.0]}


def _pair(n=1200, seed=0):
    rng = np.random.default_rng(seed)
    lx = np.log(100) + np.cumsum(rng.normal(0, 0.01, n))
    ou = np.zeros(n)
    for t in range(1, n):
        ou[t] = 0.9 * ou[t - 1] + rng.normal(0, 0.006)
    out = {}
    for sym, lp in (("ETHUSDT", 0.5 + 1.2 * lx + ou), ("BTCUSDT", lx)):
        c = np.exp(lp)
        o = np.concatenate([[c[0]], c[:-1]])
        out[sym] = pd.DataFrame({"ts": pd.date_range("2020-01-01", periods=n, freq="1D",
                                                     tz="UTC"),
                                 "open": o, "high": np.maximum(o, c), "low": np.minimum(o, c),
                                 "close": c, "volume": 1.0})
    return out


def test_walk_forward_and_pbo_on_pairs():
    bars = _pair()
    specs = {s: SYMS[s] for s in bars}
    wf = walk_forward(bars, PairsZScore(), specs, GRID, train_bars=400, test_bars=200)
    assert len(wf.folds) == 4 and wf.n_trials == 16
    assert len(wf.oos_returns) == 800
    assert wf.summary["mean_oos_sharpe"] > 0.5  # planted edge survives out of sample
    assert wf.summary["oos_buy_hold_return"] is None  # no single market to hold
    m = returns_matrix(bars, PairsZScore(), specs, GRID)
    assert m.shape == (1200, 4)


def _legs(n_legs, n=1300):
    syms = ["SPY", "QQQ", "IWM", "AAPL", "MSFT"][:n_legs]
    return {s: SyntheticProvider(seed=i + 1, annual_vol=0.3).fetch_bars(
        SYMS[s], Timeframe.D1, utc(2019, 1, 1), utc(2024, 1, 1)).iloc[:n].reset_index(drop=True)
        for i, s in enumerate(syms)}


@pytest.mark.parametrize("key", sorted(all_multi_strategies()))
def test_every_multi_strategy_is_causal(key):
    """Walk-forward works each signal out once over the whole history; that is only fair
    because adding later bars never changes an earlier weight."""
    s = all_multi_strategies()[key]()
    bars = _legs(s.legs or 3)
    p = s.params()
    full = s.target_weights({k: v.copy() for k, v in bars.items()}, p)
    part = s.target_weights({k: v.iloc[:900].copy() for k, v in bars.items()}, p)
    pd.testing.assert_frame_equal(full.iloc[:900], part)


def test_unseen_windows_match_replaying_all_earlier_history():
    bars = _pair()
    specs = {s: SYMS[s] for s in bars}
    wf = walk_forward(bars, PairsZScore(), specs, {"lookback": [60]}, train_bars=400,
                      test_bars=200)
    p = PairsZScore().params({"lookback": 60})
    for start in range(400, 1200, 200):  # the slow way: replay everything before the window
        _, eq = _segment(bars, PairsZScore(), specs, p, None, start, start + 200, start)
        np.testing.assert_allclose(wf.oos_returns.loc[eq.index].to_numpy(),
                                   eq.pct_change().fillna(0.0).to_numpy(), rtol=0, atol=1e-12)


def test_validation_tournament_and_leaderboard(session):
    p = SyntheticProvider(annual_vol=0.4)
    for sym in ("SPY", "ETHUSDT", "BTCUSDT"):
        ingest_bars(session, p, SYMS[sym], Timeframe.D1, utc(2018, 1, 1), utc(2024, 1, 1))
    session.commit()
    exp = run_validation(session, PairsZScore(), symbol="ETHUSDT/BTCUSDT",
                         timeframe=Timeframe.D1, source="synthetic", start=utc(2018, 1, 1),
                         end=utc(2024, 1, 1), grid=GRID, train_bars=500, test_bars=250,
                         mc_runs=200)
    dm = exp.data_manifest
    assert dm["symbols"] == ["ETHUSDT", "BTCUSDT"] and len(dm["data_sha256_by_symbol"]) == 2
    assert "pbo" in exp.metrics and exp.metrics["folds"] >= 3
    entries = run_tournament(
        session, [all_strategies()["TF-SMA-001"](), PairsZScore()],
        symbols=["SPY", "ETHUSDT/BTCUSDT"], timeframe=Timeframe.D1, source="synthetic",
        start=utc(2018, 1, 1), end=utc(2024, 1, 1), train_bars=500, test_bars=250,
        regime_filter=True)
    pairs = {(e.strategy_key, e.symbol) for e in entries}
    assert ("STAT-PAIR-001", "ETHUSDT/BTCUSDT") in pairs and ("TF-SMA-001", "SPY") in pairs
    assert not any(k == "STAT-PAIR-001" and s == "SPY" for k, s in pairs)
    assert not any(k.startswith("TF-SMA-001") and "/" in s for k, s in pairs)
    assert not any(k == "STAT-PAIR-001+RF" for k, _ in pairs)
    lb = leaderboard(session)
    row = lb[lb["strategy"] == "STAT-PAIR-001"].iloc[0]
    assert row["symbol"] == "ETHUSDT/BTCUSDT" and row["asset_class"] == "crypto"


def test_cli_validate_pair(db_url, monkeypatch, capsys):
    from apex.core.config import get_settings

    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        for sym in ("ETHUSDT", "BTCUSDT"):
            assert cli(["ingest", "--provider", "synthetic", "--symbol", sym, "--timeframe",
                        "1d", "--start", "2019-01-01", "--end", "2023-01-01"]) == 0
        base = ["validate", "--strategy", "STAT-PAIR-001", "--symbol", "ETHUSDT/BTCUSDT",
                "--source", "synthetic", "--start", "2019-01-01", "--end", "2023-01-01",
                "--train-bars", "400", "--test-bars", "200"]
        assert cli(base + ["--grid", "entry_z=1.5,2.0"]) == 0
        assert cli(base + ["--regime-filter"]) == 2
    finally:
        get_settings.cache_clear()
    assert "walk-forward (SIMULATED" in capsys.readouterr().out
