"""H41 ETF vs basket (STAT-BASKET-001): fade the gap between an ETF and its holdings."""

import numpy as np
import pandas as pd
import pytest

from apex.backtest.engine import BacktestConfig
from apex.backtest.portfolio import run_portfolio_backtest
from apex.domain.instruments import by_symbol
from apex.strategies.multi import EtfVsBasket, all_multi_strategies

SYMS = by_symbol()
LEGS = ("QQQ", "AAPL", "MSFT", "NVDA", "AMZN")


def _bars(logp):
    close = np.exp(np.asarray(logp, float))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"ts": pd.date_range("2025-01-02 14:30", periods=len(close),
                                             freq="5min", tz="UTC"),
                         "open": open_, "high": np.maximum(open_, close),
                         "low": np.minimum(open_, close), "close": close, "volume": 1.0})


def _market(seed=0, n=3000, linked=True):
    rng = np.random.default_rng(seed)
    common = np.cumsum(rng.normal(0, 0.002, n))
    hold = [np.log(100) + common + np.cumsum(rng.normal(0, 0.002, n)) for _ in LEGS[1:]]
    if linked:
        gap = np.zeros(n)
        for t in range(1, n):
            gap[t] = 0.9 * gap[t - 1] + rng.normal(0, 0.0015)
        etf = np.mean(hold, axis=0) + gap
    else:
        etf = np.log(100) + common + np.cumsum(rng.normal(0, 0.002, n))
    return {s: _bars(lp) for s, lp in zip(LEGS, [etf, *hold], strict=True)}


def test_registered_and_needs_etf_plus_two():
    assert all_multi_strategies()["STAT-BASKET-001"] is EtfVsBasket
    specs = {s: SYMS[s] for s in LEGS}
    with pytest.raises(ValueError, match="at least 2 holdings"):
        run_portfolio_backtest(dict(list(_market().items())[:2]), EtfVsBasket(), specs)


def test_weights_causal_hedged_and_spread_over_the_basket():
    s = EtfVsBasket()
    p = s.params()
    bars = _market()
    full = s.target_weights(bars, p)
    part = s.target_weights({k: v.iloc[:1500].copy() for k, v in bars.items()}, p)
    pd.testing.assert_frame_equal(full.iloc[:1500], part)
    on = full[full["QQQ"] != 0]
    assert len(on) > 100
    hold = on[list(LEGS[1:])]
    assert np.allclose(hold.sub(hold.iloc[:, 0], axis=0), 0)  # equal weight across holdings
    assert (np.sign(hold.iloc[:, 0]) == -np.sign(on["QQQ"])).all()  # always hedged
    assert (on.abs().sum(axis=1) <= p["gross"] + 1e-9).all()


def test_profits_when_etf_tracks_basket_not_when_unrelated():
    specs = {s: SYMS[s] for s in LEGS}
    cheap = BacktestConfig(cost_multiplier=0.1)
    good = [run_portfolio_backtest(_market(seed), EtfVsBasket(), specs, config=cheap).metrics
            for seed in range(5)]
    assert all(m["trades"] >= 20 and m["total_return"] > 0.05 for m in good)
    bad = [run_portfolio_backtest(_market(seed, linked=False), EtfVsBasket(), specs,
                                  config=cheap).metrics["total_return"] for seed in range(5)]
    # 5-minute Sharpe ratios annualise to extreme values, so compare returns
    assert np.mean(bad) < np.mean([m["total_return"] for m in good]) / 3
