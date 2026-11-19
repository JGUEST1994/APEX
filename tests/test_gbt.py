"""Gradient-boosted trees (dependency-free) and the H48 filter strategy ML-GBT-001."""

import numpy as np
import pandas as pd
import pytest

from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.ml.cv import classification_report, walk_forward_proba
from apex.ml.labels import triple_barrier
from apex.ml.model import GradientBoostedTrees, LogisticModel
from apex.strategies.registry import all_strategies
from tests.conftest import utc


def test_gbt_learns_an_interaction_logistic_cannot(tmp_path):
    rng = np.random.default_rng(0)
    x = rng.normal(size=(2500, 3))
    y = ((x[:, 0] * x[:, 1] > 0) ^ (rng.random(2500) < 0.1)).astype(float)
    tr, te = slice(0, 2000), slice(2000, None)
    g = GradientBoostedTrees(features=["a", "b", "c"]).fit(x[tr], y[tr])
    acc_g = ((g.predict_proba(x[te]) > 0.5) == y[te]).mean()
    acc_l = ((LogisticModel().fit(x[tr], y[tr]).predict_proba(x[te]) > 0.5) == y[te]).mean()
    assert acc_g > 0.8 and acc_l < 0.6
    g.save(tmp_path / "g.json")
    g2 = GradientBoostedTrees.load(tmp_path / "g.json")
    assert np.allclose(g.predict_proba(x[:50]), g2.predict_proba(x[:50]))
    assert g2.features == ["a", "b", "c"]
    with pytest.raises(ValueError):
        GradientBoostedTrees().fit(x, np.zeros(2500))


def test_gbt_is_deterministic_and_bounded():
    rng = np.random.default_rng(1)
    x, y = rng.normal(size=(800, 4)), (rng.random(800) < 0.4).astype(float)
    a = GradientBoostedTrees(n_trees=20).fit(x, y).predict_proba(x)
    b = GradientBoostedTrees(n_trees=20).fit(x, y).predict_proba(x)
    assert np.array_equal(a, b) and ((a > 0) & (a < 1)).all()


def test_walk_forward_with_gbt_finds_planted_signal_and_rejects_unknown_model():
    rng = np.random.default_rng(3)
    n = 1500
    signal = rng.normal(size=n)
    r = 0.004 * np.sign(signal) + rng.normal(0, 0.004, n)
    close = 100 * np.exp(np.cumsum(np.concatenate([[0], r[:-1]])))
    bars = pd.DataFrame({"ts": pd.date_range("2020-01-01", periods=n, freq="1D", tz="UTC"),
                         "open": close, "high": close, "low": close, "close": close,
                         "volume": 1.0})
    x = pd.DataFrame({"signal": signal}, index=bars.index)
    lab = triple_barrier(bars, horizon=1, vol_n=20)
    p = walk_forward_proba(x, lab, min_train=300, retrain=100, model="gbt")
    assert p.iloc[:300].isna().all()
    assert classification_report(p, lab)["accuracy"] > 0.7
    with pytest.raises(ValueError, match="unknown model"):
        walk_forward_proba(x, lab, min_train=300, retrain=100, model="forest")


def test_gbt_filter_only_keeps_sma_trades_the_model_agrees_with():
    strat = all_strategies()["ML-GBT-001"]()
    bars = SyntheticProvider(annual_vol=0.4).fetch_bars(by_symbol()["SPY"], Timeframe.D1,
                                                        utc(2018, 1, 1), utc(2023, 1, 1))
    p = strat.params()
    pos = strat.target_position(bars, p)
    sma = all_strategies()["TF-SMA-001"]()
    base = sma.target_position(bars, sma.params({"fast": p["fast"], "slow": p["slow"]}))
    on = pos != 0
    assert on.any() and (pos[on] == base[on]).all()  # never trades against the crossover
    assert on.sum() < (base != 0).sum()  # and filters some of it out
    assert (pos.iloc[: p["min_train"]] == 0).all()  # nothing before the first model
