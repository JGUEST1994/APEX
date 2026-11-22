"""H54 internal bar strength on a fund basket (MR-IBS-BASKET-001)."""

import numpy as np
import pandas as pd

from apex.backtest.engine import BacktestConfig, run_backtest
from apex.backtest.portfolio import run_portfolio_backtest
from apex.domain.instruments import by_symbol
from apex.strategies.builtin2 import InternalBarStrength
from apex.strategies.multi import IbsBasket, all_multi_strategies

SYMS = by_symbol()
BASKET = ("SPY", "QQQ", "IWM", "DIA", "XLF", "XLK")


def _bars(seed, n=800):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    open_ = np.concatenate([[close[0]], close[:-1]]) * np.exp(rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.006, n)))
    return pd.DataFrame({"ts": pd.date_range("2018-01-01", periods=n, freq="1D", tz="UTC"),
                         "open": open_, "high": high, "low": low, "close": close,
                         "volume": 1.0})


def _basket():
    return {s: _bars(i) for i, s in enumerate(BASKET)}


def test_each_fund_follows_the_single_market_rules_at_a_fixed_share():
    assert all_multi_strategies()["MR-IBS-BASKET-001"] is IbsBasket
    s, bars = IbsBasket(), _basket()
    p = s.params()
    w = s.target_weights(bars, p)
    single = InternalBarStrength()
    for sym, b in bars.items():
        pos = single.target_position(b, {"threshold": p["threshold"],
                                         "max_bars": p["max_bars"]})
        np.testing.assert_allclose(w[sym].to_numpy(), pos.to_numpy() / len(BASKET))
    assert set(np.unique(w.to_numpy().round(12))) == {0.0, round(1 / len(BASKET), 12)}
    assert (w.sum(axis=1) <= p["gross"] + 1e-12).all()
    assert (w.astype(bool).sum(axis=1) >= 2).any()  # several funds at once, not one at a time


def test_weights_are_causal():
    s, bars = IbsBasket(), _basket()
    full = s.target_weights(bars, s.params())
    part = s.target_weights({k: v.iloc[:500].copy() for k, v in bars.items()}, s.params())
    pd.testing.assert_frame_equal(full.iloc[:500], part)


def test_the_basket_falls_less_than_the_typical_single_fund():
    bars, cfg = _basket(), BacktestConfig()
    specs = {s: SYMS[s] for s in BASKET}
    res = run_portfolio_backtest(bars, IbsBasket(), specs, config=cfg)
    singles = [run_backtest(bars[s], InternalBarStrength(), SYMS[s], None, cfg)
               for s in BASKET]
    basket_dd = res.metrics["max_drawdown"]
    single_dd = np.median([r.metrics["max_drawdown"] for r in singles])
    assert len(res.trades) > 100
    assert basket_dd > single_dd  # drawdowns are negative: closer to zero is smaller
