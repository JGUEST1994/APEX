"""H07 cross-sectional momentum (MOM-XSEC-001): rank a basket, long winners, short losers."""

import numpy as np
import pandas as pd
import pytest

from apex.backtest.engine import BacktestConfig
from apex.backtest.portfolio import run_portfolio_backtest
from apex.domain.instruments import by_symbol
from apex.strategies.multi import CrossSectionalMomentum, all_multi_strategies

SYMS = by_symbol()
BASKET = ("SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA")


def _bars(logp):
    close = np.exp(np.asarray(logp, float))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"ts": pd.date_range("2020-01-01", periods=len(close), freq="1D",
                                             tz="UTC"),
                         "open": open_, "high": np.maximum(open_, close),
                         "low": np.minimum(open_, close), "close": close, "volume": 1.0})


def _basket(seed=0, n=900, spread=0.0015, regime=300):
    """Each name gets a drift that persists for `regime` bars, then is redrawn."""
    rng = np.random.default_rng(seed)
    out = {}
    for s in BASKET:
        drift = np.repeat(rng.normal(0, spread, n // regime + 1), regime)[:n]
        out[s] = _bars(np.log(100) + np.cumsum(drift + rng.normal(0, 0.015, n)))
    return out


def _specs():
    return {s: SYMS[s] for s in BASKET}


def test_registered_and_weights_are_causal_ranked_and_balanced():
    assert all_multi_strategies()["MOM-XSEC-001"] is CrossSectionalMomentum
    s = CrossSectionalMomentum()
    p = s.params()
    bars = _basket()
    full = s.target_weights(bars, p)
    part = s.target_weights({k: v.iloc[:500].copy() for k, v in bars.items()}, p)
    pd.testing.assert_frame_equal(full.iloc[:500], part)
    assert (full.iloc[:p["lookback"]] == 0).all().all()  # nothing until ranks exist
    live = full[(full != 0).any(axis=1)]
    assert len(live) > 600
    assert np.allclose(live.sum(axis=1), 0)  # dollar neutral
    assert np.allclose(live.abs().sum(axis=1), p["gross"])
    assert ((live > 0).sum(axis=1) == 2).all() and ((live < 0).sum(axis=1) == 2).all()
    # longs really are the names with the best lookback-to-skip return at the rebalance
    t = 252
    close = pd.DataFrame({k: v["close"] for k, v in bars.items()})
    r = close.iloc[t - p["skip"]] / close.iloc[t - p["lookback"]] - 1
    assert set(full.iloc[t][full.iloc[t] > 0].index) == set(r.nlargest(2).index)
    changes = [i for i in range(1, len(full)) if not full.iloc[i].equals(full.iloc[i - 1])]
    assert all(i % p["rebalance"] == 0 for i in changes)


def test_long_only_and_parameter_checks():
    s = CrossSectionalMomentum()
    w = s.target_weights(_basket(), s.params({"long_only": True, "top": 0.5}))
    live = w[(w != 0).any(axis=1)]
    assert (live >= 0).all().all() and ((live > 0).sum(axis=1) == 3).all()
    assert np.allclose(live.sum(axis=1), 1.0)
    with pytest.raises(ValueError):
        s.target_weights(_basket(), s.params({"skip": 200}))


def test_profits_when_winners_persist_not_on_pure_noise():
    cheap = BacktestConfig(cost_multiplier=0.2)
    trend = [run_portfolio_backtest(_basket(seed), CrossSectionalMomentum(), _specs(),
                                    config=cheap).metrics for seed in range(5)]
    assert np.mean([m["sharpe"] for m in trend]) > 1.0
    noise = [run_portfolio_backtest(_basket(seed, spread=0.0), CrossSectionalMomentum(),
                                    _specs(), config=cheap).metrics["sharpe"]
             for seed in range(5)]
    assert np.mean(noise) < 0.5
