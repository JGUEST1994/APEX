"""Phase 8 regime detection: causality, HMM recovery of known regimes, filters, CLI."""

import numpy as np
import pandas as pd
import pytest

from apex.backtest.experiments import ensure_strategy_version
from apex.cli import main as cli
from apex.db.models import Strategy as StrategyRow
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.regime.detect import (
    apply_regime_filter,
    efficiency_ratio,
    forward_vol_by_state,
    hmm_regime,
    stability,
    trend_regime,
    volatility_regime,
)
from apex.regime.filtered import RegimeFiltered
from apex.regime.hmm import GaussianHMM
from apex.strategies.registry import all_strategies
from tests.conftest import utc


def _switching_bars(seed=1, block=150, blocks=8):
    """Price path alternating calm (vol 0.5%) and turbulent (vol 3%) blocks."""
    rng = np.random.default_rng(seed)
    vols = np.repeat([0.005 if i % 2 == 0 else 0.03 for i in range(blocks)], block)
    r = rng.normal(0, vols)
    close = 100 * np.exp(np.cumsum(r))
    ts = pd.date_range("2020-01-01", periods=len(close), freq="1D", tz="UTC")
    open_ = np.concatenate([[100.0], close[:-1]])
    bars = pd.DataFrame({"ts": ts, "open": open_, "high": np.maximum(open_, close) * 1.001,
                         "low": np.minimum(open_, close) * 0.999, "close": close,
                         "volume": 1.0})
    truth = pd.Series((vols > 0.01).astype(int))
    return bars, truth


def test_hmm_recovers_known_volatility_regimes_out_of_sample():
    bars, truth = _switching_bars()
    train_end = 600
    state, model = hmm_regime(bars, train_end)
    assert model.var_[0] < model.var_[1]  # state 0 is the calm one
    oos = slice(train_end + 10, None)
    acc = (state.iloc[oos].to_numpy() == truth.iloc[oos].to_numpy()).mean()
    assert acc > 0.85
    fwd = forward_vol_by_state(bars, state, train_end)
    assert fwd["1"] > 2 * fwd["0"]
    assert model.expected_durations().min() > 10  # persistent, not flickering


def test_hmm_filter_is_causal():
    bars, _ = _switching_bars()
    r = np.log(bars["close"]).diff().to_numpy()
    m = GaussianHMM().fit(r[1:600])
    full, part = m.filter(r), m.filter(r[:700])
    assert np.allclose(full[:700], part)
    assert np.allclose(full.sum(axis=1), 1.0)


def test_hmm_needs_data():
    with pytest.raises(ValueError):
        GaussianHMM().fit(np.zeros(5))


@pytest.mark.parametrize("fn", [trend_regime, volatility_regime])
def test_rule_regimes_are_causal(fn):
    bars = SyntheticProvider().fetch_bars(by_symbol()["SPY"], Timeframe.D1, utc(2018, 1, 1),
                                          utc(2024, 1, 1))
    full, part = fn(bars), fn(bars.iloc[:800].copy())
    assert full.iloc[:800].equals(part)


def test_efficiency_ratio_and_trend_labels():
    up = pd.Series(np.arange(1.0, 60.0))
    assert efficiency_ratio(up, 10).dropna().eq(1.0).all()
    zigzag = pd.Series([100 + (i % 2) for i in range(60)], dtype=float)
    assert efficiency_ratio(zigzag, 10).dropna().max() < 0.2
    bars = pd.DataFrame({"close": up})
    assert set(trend_regime(bars, 10).dropna()) == {"trending"}


def test_volatility_regime_flags_turbulence():
    bars, truth = _switching_bars()
    lab = volatility_regime(bars, n=20, lookback=300)
    calm = lab[(truth == 0) & lab.notna()].iloc[200:]
    wild = lab[(truth == 1) & lab.notna()].iloc[200:]
    assert (wild == "high_vol").mean() > (calm == "high_vol").mean() + 0.3


def test_stability_and_filter():
    s = pd.Series(["a"] * 5 + ["b"] * 5)
    st = stability(s)
    assert st["switch_rate"] == pytest.approx(1 / 9) and st["mean_run"] == 5
    pos = pd.Series([1.0, 1.0, -1.0, 0.0])
    lab = pd.Series(["trending", "range", None, "trending"])
    assert apply_regime_filter(pos, lab, ("trending",)).tolist() == [1.0, 0.0, 0.0, 0.0]
    assert apply_regime_filter(pos, lab, ("any",)).equals(pos)


def test_regime_filtered_strategy(session):
    inner = all_strategies()["BO-DONCH-001"]()
    rf = RegimeFiltered(inner)
    assert rf.meta.key == "BO-DONCH-001+RF" and "rf_n" in rf.params()
    bars = SyntheticProvider(annual_vol=0.6).fetch_bars(by_symbol()["SPY"], Timeframe.D1,
                                                        utc(2020, 1, 1), utc(2024, 1, 1))
    p = rf.params()
    pos, raw = rf.target_position(bars, p), inner.target_position(bars, inner.params())
    labels = trend_regime(bars)
    assert (pos[labels != "trending"] == 0).all()
    assert pos[labels == "trending"].equals(raw[labels == "trending"])
    # causal
    assert pos.iloc[:500].equals(rf.target_position(bars.iloc[:500].copy(), p))
    # fingerprint covers the wrapped strategy's code too
    assert inner.source_code() in rf.source_code()
    ver = ensure_strategy_version(session, rf)
    assert session.get(StrategyRow, ver.strategy_id).key == "BO-DONCH-001+RF"


def test_cli_regimes_and_regime_filter(db_url, monkeypatch, capsys):
    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    from apex.core.config import get_settings
    get_settings.cache_clear()
    try:
        assert cli(["ingest", "--provider", "synthetic", "--symbol", "SPY", "--timeframe", "1d",
                    "--start", "2018-01-01", "--end", "2024-01-01"]) == 0
        assert cli(["regimes", "--symbol", "SPY", "--source", "synthetic",
                    "--start", "2018-01-01", "--end", "2024-01-01"]) == 0
        assert cli(["backtest", "--strategy", "BO-DONCH-001", "--symbol", "SPY",
                    "--source", "synthetic", "--start", "2018-01-01", "--end", "2024-01-01",
                    "--regime-filter"]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "switch_rate" in out and "OOS mean forward vol" in out
