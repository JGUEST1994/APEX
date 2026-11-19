"""H50 meta-labelling: causality, only filters primary trades, learns a planted edge."""

import numpy as np
import pandas as pd

from apex.backtest.engine import BacktestConfig, run_backtest
from apex.domain.instruments import by_symbol
from apex.ml.meta import MetaLabeled, events
from apex.strategies.base import Strategy
from apex.strategies.registry import all_strategies

SPY = by_symbol()["SPY"]


def _bars(n=2000, drift=0.0015, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.01, n)))
    open_ = np.concatenate([[100.0], close[:-1]])
    return pd.DataFrame({"ts": pd.date_range("2015-01-01", periods=n, freq="1D", tz="UTC"),
                         "open": open_, "high": np.maximum(open_, close) * 1.001,
                         "low": np.minimum(open_, close) * 0.999, "close": close,
                         "volume": 1.0})


class Alternator(Strategy):
    """Long for 10 bars, flat 2, short for 10 bars, flat 2, ... (no skill)."""

    meta = all_strategies()["TF-SMA-001"].meta.__class__(
        **{**all_strategies()["TF-SMA-001"].meta.__dict__, "key": "T-ALT",
           "default_params": {}})

    def target_position(self, bars, params):
        cyc = np.arange(len(bars)) % 24
        return pd.Series(np.select([cyc < 10, (cyc >= 12) & (cyc < 22)], [1.0, -1.0], 0.0),
                         index=bars.index)


def test_events_are_new_sides():
    assert events(pd.Series([0, 1, 1, 0, -1, 1, 1, 0])).tolist() == [1, 4, 5]


def test_meta_is_causal_and_only_filters():
    bars = _bars()
    m = MetaLabeled(Alternator())
    p = m.params()
    full = m.target_position(bars, p)
    part = m.target_position(bars.iloc[:1200].copy(), p)
    assert full.iloc[:1200].equals(part)
    prim = Alternator().target_position(bars, {})
    assert ((full == 0) | (full == prim)).all()  # never adds or flips a trade


def test_meta_learns_to_skip_losing_side():
    bars = _bars()  # steady uptrend: longs win, shorts lose
    m = MetaLabeled(Alternator())
    pos = m.target_position(bars, m.params())
    late = pos.iloc[800:]
    prim = Alternator().target_position(bars, {}).iloc[800:]
    assert (late > 0).sum() > 0.8 * (prim > 0).sum()  # keeps most longs
    assert (late < 0).sum() < 0.3 * (prim < 0).sum()  # drops most shorts
    rep = m.last_report
    assert rep["scored"] > 50 and rep["taken"] < rep["scored"]
    cfg = BacktestConfig()
    a = run_backtest(bars, Alternator(), SPY, {}, cfg).metrics["sharpe"]
    b = run_backtest(bars, m, SPY, m.params(), cfg).metrics["sharpe"]
    assert b > a


def test_warmup_follow_or_skip():
    bars = _bars(300)
    m = MetaLabeled(Alternator())
    prim = Alternator().target_position(bars, {})
    follow = m.target_position(bars, m.params({"meta_min_events": 10_000}))
    skip = m.target_position(bars, m.params({"meta_min_events": 10_000, "meta_warmup": "skip"}))
    assert follow.equals(prim) and (skip == 0).all()


def test_registered_reference_strategy():
    s = all_strategies()["ML-META-001"]()
    assert s.meta.key == "ML-META-001" and "meta_threshold" in s.params()
    assert s.inner.meta.key == "BO-DONCH-001"
    assert s.inner.source_code() in s.source_code()
