"""Phase 7 ML: labels, purged CV, model, walk-forward leakage guards."""

import numpy as np
import pandas as pd
import pytest

from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.ml.cv import classification_report, purged_kfold, walk_forward_proba
from apex.ml.features import make_features
from apex.ml.labels import triple_barrier
from apex.ml.model import LogisticModel
from tests.conftest import utc


def _bars(close):
    close = np.asarray(close, float)
    ts = pd.date_range("2020-01-01", periods=len(close), freq="1D", tz="UTC")
    return pd.DataFrame({"ts": ts, "open": close, "high": close, "low": close,
                         "close": close, "volume": 1.0})


def test_triple_barrier_labels():
    rng = np.random.default_rng(0)
    noise = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 30)))
    up = noise[-1] * np.exp(np.arange(1, 11) * 0.05)  # strong rise -> upper barrier
    lab = triple_barrier(_bars(np.concatenate([noise, up])), horizon=5, vol_n=20)
    assert lab["label"].iloc[29] == 1 and lab["end"].iloc[29] == 34
    assert lab["label"].iloc[-5:].isna().all()  # no full horizon at the end
    assert lab["label"].iloc[:20].isna().all()  # no trailing vol yet
    down = triple_barrier(_bars(np.concatenate([noise, noise[-1] * np.exp(-np.arange(1, 11)
                                                                          * 0.05)])), 5)
    assert down["label"].iloc[29] == -1


def test_purged_kfold_drops_overlapping_and_embargoed_samples():
    ends = np.arange(100) + 5
    for train, test in purged_kfold(ends, n_splits=4, embargo=3):
        t0, t1 = test[0], test[-1]
        assert not ((train <= t1) & (ends[train] >= t0)).any()
        assert not ((train > t1) & (train <= t1 + 3)).any()
        assert len(set(train) & set(test)) == 0


def test_logistic_model_learns_and_round_trips(tmp_path):
    rng = np.random.default_rng(1)
    x = rng.normal(size=(2000, 3))
    y = (x[:, 0] - 0.5 * x[:, 1] + rng.normal(0, 0.5, 2000) > 0).astype(float)
    m = LogisticModel(l2=1.0, features=["a", "b", "c"]).fit(x, y)
    acc = ((m.predict_proba(x) > 0.5) == y).mean()
    assert acc > 0.85 and m.w_[1] > 0 > m.w_[2]
    m.save(tmp_path / "m.json")
    m2 = LogisticModel.load(tmp_path / "m.json")
    assert np.allclose(m.predict_proba(x[:10]), m2.predict_proba(x[:10]))
    with pytest.raises(ValueError):
        LogisticModel().fit(x, np.ones(2000))


def test_features_are_causal():
    bars = SyntheticProvider().fetch_bars(by_symbol()["SPY"], Timeframe.D1, utc(2018, 1, 1),
                                          utc(2023, 1, 1))
    full, part = make_features(bars), make_features(bars.iloc[:700].copy())
    pd.testing.assert_frame_equal(full.iloc[:700], part)


def test_walk_forward_never_trains_on_unfinished_labels(monkeypatch):
    bars = SyntheticProvider().fetch_bars(by_symbol()["SPY"], Timeframe.D1, utc(2015, 1, 1),
                                          utc(2023, 1, 1))
    x, lab = make_features(bars), triple_barrier(bars, horizon=10)
    seen = []
    orig = LogisticModel.fit

    def spy(self, xx, yy):
        seen.append(len(xx))
        return orig(self, xx, yy)
    monkeypatch.setattr(LogisticModel, "fit", spy)
    proba = walk_forward_proba(x, lab, min_train=500, retrain=250)
    assert proba.iloc[:500].isna().all() and proba.iloc[600:].notna().mean() > 0.95
    ends = lab["end"].to_numpy()
    for k, t in enumerate(range(500, len(x), 250)):
        eligible = (~x.isna().any(axis=1).to_numpy() & lab["label"].notna().to_numpy()
                    & (lab["label"].to_numpy() != 0) & (ends >= 0) & (ends < t))
        assert seen[k] == eligible.sum()
    # random-walk data: no real edge, so out-of-sample accuracy is near the base rate
    rep = classification_report(proba, lab)
    assert rep["n"] > 1000 and abs(rep["accuracy"] - 0.5) < 0.08


def test_walk_forward_finds_planted_signal():
    rng = np.random.default_rng(3)
    n = 1500
    signal = rng.normal(size=n)
    r = 0.004 * np.sign(signal) + rng.normal(0, 0.004, n)  # sign of signal drives next bar
    close = 100 * np.exp(np.cumsum(np.concatenate([[0], r[:-1]])))
    bars = _bars(close)
    x = pd.DataFrame({"signal": signal}, index=bars.index)
    lab = triple_barrier(bars, horizon=1, vol_n=20)
    rep = classification_report(walk_forward_proba(x, lab, min_train=300, retrain=100), lab)
    assert rep["accuracy"] > 0.7
