"""Multi-instrument strategies: portfolio backtester and the pairs strategy."""

import numpy as np
import pandas as pd
import pytest

from apex.backtest.engine import BacktestConfig, run_backtest
from apex.backtest.portfolio import run_portfolio_backtest
from apex.domain.instruments import by_symbol
from apex.strategies.multi import MultiStrategy, PairsZScore, align, all_multi_strategies
from apex.strategies.registry import all_strategies

SYMS = by_symbol()


def _bars(close, start="2020-01-01", freq="1D"):
    close = np.asarray(close, float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"ts": pd.date_range(start, periods=len(close), freq=freq, tz="UTC"),
                         "open": open_, "high": np.maximum(open_, close),
                         "low": np.minimum(open_, close), "close": close, "volume": 1.0})


def _pair(seed=0, n=1500, coint=True):
    rng = np.random.default_rng(seed)
    lx = np.log(100) + np.cumsum(rng.normal(0, 0.01, n))
    if coint:
        ou = np.zeros(n)
        for t in range(1, n):
            ou[t] = 0.9 * ou[t - 1] + rng.normal(0, 0.006)
        ly = 0.5 + 1.2 * lx + ou
    else:
        ly = np.log(100) + np.cumsum(rng.normal(0, 0.01, n))
    return {"ETHUSDT": _bars(np.exp(ly)), "BTCUSDT": _bars(np.exp(lx))}


def test_pairs_is_registered_separately_and_refuses_single_use():
    assert "STAT-PAIR-001" in all_multi_strategies()
    assert "STAT-PAIR-001" not in all_strategies()
    with pytest.raises(TypeError):
        PairsZScore().target_position(_bars([1, 2, 3]), PairsZScore().params())


def test_pairs_weights_are_causal_and_hedged():
    bars = _pair()
    s = PairsZScore()
    p = s.params()
    full = s.target_weights(bars, p)
    part = s.target_weights({k: v.iloc[:900].copy() for k, v in bars.items()}, p)
    pd.testing.assert_frame_equal(full.iloc[:900], part)
    on = full[(full != 0).any(axis=1)]
    assert len(on) > 50
    assert (np.sign(on["ETHUSDT"]) == -np.sign(on["BTCUSDT"])).all()  # always hedged
    assert (on.abs().sum(axis=1) <= p["gross"] + 1e-9).all()


def test_pairs_profits_on_cointegrated_pair_not_on_unrelated_ones():
    specs = {s: SYMS[s] for s in ("ETHUSDT", "BTCUSDT")}
    cheap = BacktestConfig(cost_multiplier=0.2)
    good = [run_portfolio_backtest(_pair(seed), PairsZScore(), specs, config=cheap).metrics
            for seed in range(4)]
    assert all(m["trades"] >= 20 and m["total_return"] > 0.1 and m["sharpe"] > 1.5
               for m in good)
    bad = [run_portfolio_backtest(_pair(seed, coint=False), PairsZScore(), specs,
                                  config=cheap).metrics["sharpe"] for seed in range(4)]
    assert np.mean(bad) < 1.0  # unrelated random walks: no real edge


class _OneLeg(MultiStrategy):
    legs = 1
    meta = PairsZScore.meta.__class__(**{**PairsZScore.meta.__dict__, "key": "T-ONE",
                                        "default_params": {}})

    def target_weights(self, bars, params):
        (sym, df), = bars.items()
        w = np.where(df["close"].pct_change(5) > 0, 1.0, -1.0)
        return pd.DataFrame({sym: w}, index=df.index)


class _Single(all_strategies()["TF-SMA-001"]):  # same rule as _OneLeg, single-asset API
    def target_position(self, bars, params):
        return pd.Series(np.where(bars["close"].pct_change(5) > 0, 1.0, -1.0),
                         index=bars.index)


def test_portfolio_engine_matches_single_engine_for_one_leg():
    bars = _bars(100 * np.exp(np.cumsum(np.random.default_rng(2).normal(0, 0.01, 400))))
    spy = SYMS["SPY"]
    a = run_portfolio_backtest({"SPY": bars}, _OneLeg(), {"SPY": spy})
    b = run_backtest(bars, _Single(), spy)
    assert np.allclose(a.equity.to_numpy(), b.equity.to_numpy())
    assert a.total_costs == pytest.approx(b.total_costs)
    assert len(a.trades) == len(b.trades)
    assert [round(t.pnl, 6) for t in a.trades] == [round(t.pnl, 6) for t in b.trades]


def test_fills_at_next_open_and_alignment():
    bars = _pair(n=400)
    bars["BTCUSDT"] = bars["BTCUSDT"].drop(index=[10, 11]).reset_index(drop=True)
    al = align(bars)
    assert len(al["ETHUSDT"]) == len(al["BTCUSDT"]) == 398
    res = run_portfolio_backtest(bars, PairsZScore(), {s: SYMS[s] for s in bars})
    w = PairsZScore().target_weights(al, PairsZScore().params())
    ts = pd.DatetimeIndex(al["ETHUSDT"]["ts"])
    changed = [i for i in range(1, len(w)) if not w.iloc[i].equals(w.iloc[i - 1])]
    first = res.fills["ts"].iloc[0]
    assert first == ts[changed[0] + 1]  # decided at the close, filled at the next open
    opens = al["ETHUSDT"].set_index("ts")["open"]
    eth = res.fills[res.fills["symbol"] == "ETHUSDT"]
    assert (eth["price"].to_numpy() == opens.loc[eth["ts"]].to_numpy()).all()


def test_wrong_leg_count_rejected():
    with pytest.raises(ValueError):
        run_portfolio_backtest({"SPY": _bars(range(1, 50))}, PairsZScore(),
                               {"SPY": SYMS["SPY"]})


def test_cli_backtest_pair_records_experiment(db_url, monkeypatch, capsys):
    from apex.cli import main as cli
    from apex.core.config import get_settings

    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        for sym in ("ETHUSDT", "BTCUSDT"):
            assert cli(["ingest", "--provider", "synthetic", "--symbol", sym, "--timeframe",
                        "1d", "--start", "2021-01-01", "--end", "2024-01-01"]) == 0
        assert cli(["backtest-pair", "--symbols", "ETHUSDT,BTCUSDT", "--source", "synthetic",
                    "--start", "2021-01-01", "--end", "2024-01-01",
                    "--param", "lookback=40"]) == 0
        assert cli(["backtest-pair", "--strategy", "NOPE", "--symbols", "A,B", "--source",
                    "synthetic", "--start", "2021-01-01", "--end", "2024-01-01"]) == 2
        assert cli(["strategies"]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "(SIMULATED) ETHUSDT/BTCUSDT" in out and "sharpe" in out
    assert "STAT-PAIR-001" in out and "[2 instruments]" in out
