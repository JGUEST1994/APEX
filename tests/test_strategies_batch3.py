"""Batch 3: H08 intraday momentum, H10 crypto weekend momentum, H44 vol-regime switch,
H49 HMM regime gating, H52 round-number fade. (Look-ahead and "trades on some data" checks
for these run in test_strategies_research_api.py with every registered strategy.)"""

import numpy as np
import pandas as pd

from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.strategies import indicators as ind
from apex.strategies.registry import all_strategies
from tests.conftest import utc

S = all_strategies()


def _run(key, bars, **over):
    s = S[key]()
    return s.target_position(bars, s.params(over))


def _frame(ts, close):
    close = np.asarray(close, float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"ts": ts, "open": open_, "high": np.maximum(open_, close),
                         "low": np.minimum(open_, close), "close": close, "volume": 1.0})


def test_intraday_momentum_holds_first_half_hour_sign_over_the_last_half_hour():
    bars = SyntheticProvider(annual_vol=0.4).fetch_bars(by_symbol()["SPY"], Timeframe.M5,
                                                        utc(2025, 3, 3), utc(2025, 3, 15))
    pos = _run("MOM-ID30-001", bars)
    left = ind.bars_left_in_session(bars, "us_equity_rth")
    assert (pos[(left < 1) | (left > 6)] == 0).all()  # only bars 6..1 before the close
    key = ind.session_keys(bars, "us_equity_rth")
    checked = 0
    for k, g in bars.groupby(key):
        prev = bars["close"][key < k]
        if prev.empty or len(g) < 78:
            continue
        r = g["close"].iloc[5] / prev.iloc[-1] - 1  # close of the 6th 5-minute bar
        held = pos.loc[g.index][left.loc[g.index].between(1, 6)]
        assert (held == np.sign(r)).all()
        checked += 1
    assert checked >= 5
    long_only = _run("MOM-ID30-001", bars, allow_short=False)
    assert (long_only >= 0).all()


def test_crypto_weekend_momentum_is_only_held_over_the_weekend():
    ts = pd.date_range("2025-03-03", periods=24 * 21, freq="1h", tz="UTC")  # Mon, 3 weeks
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.002, len(ts))
    fri = (ts.day_of_week == 4) & (ts.hour < 20)
    r[fri] = 0.003  # strong Friday rally -> long into the weekend
    bars = _frame(ts, 100 * np.exp(np.cumsum(r)))
    pos = _run("MOM-WKND-001", bars, atr_mult=50.0)  # wide stop: test the time rules
    wk = pd.Series((ts.day_of_week == 4) & (ts.hour >= 20) | (ts.day_of_week >= 5), index=bars.index)
    assert (pos[~wk.to_numpy()] == 0).all()
    entries = bars["ts"][(pos != 0) & (pos.shift(1, fill_value=0) == 0)]
    assert list(entries.dt.strftime("%a %H")) == ["Fri 20"] * 3
    assert (pos[wk.to_numpy()] == 1).all()


def test_vol_switch_picks_trend_when_vol_rises_else_fade():
    bars = SyntheticProvider(annual_vol=0.5).fetch_bars(by_symbol()["SPY"], Timeframe.D1,
                                                        utc(2018, 1, 1), utc(2023, 1, 1))
    pos = _run("VOL-SWITCH-001", bars)
    rv = np.log(bars["close"]).diff().rolling(20, min_periods=20).std()
    rising, known = rv > rv.shift(20), rv.notna() & rv.shift(20).notna()
    sma, bb = S["TF-SMA-001"](), S["MR-BB-001"]()
    trend = sma.target_position(bars, sma.params())
    fade = bb.target_position(bars, bb.params())
    assert (pos[known & rising] == trend[known & rising]).all()
    assert (pos[known & ~rising] == fade[known & ~rising]).all()
    assert (pos[~known] == 0).all() and (rising & known).any() and (~rising & known).any()


def test_hmm_gate_uses_momentum_when_calm_and_rsi2_when_turbulent():
    rng = np.random.default_rng(4)
    vol = np.where((np.arange(1200) // 150) % 2 == 0, 0.006, 0.03)  # calm/turbulent blocks
    ts = pd.date_range("2018-01-01", periods=1200, freq="1D", tz="UTC")
    bars = _frame(ts, 100 * np.exp(np.cumsum(rng.normal(0.0003, vol))))
    pos = _run("ML-HMM-001", bars)
    assert (pos.iloc[:300] == 0).all()
    from apex.regime.detect import hmm_regime

    state, _ = hmm_regime(bars, train_end=300, n_states=2, seed=0)
    calm = (state == 0).to_numpy()
    assert 0.3 < calm[300:].mean() < 0.7  # the HMM really separates the blocks
    ts_ = S["MOM-TSMOM-001"]()
    rsi = S["MR-RSI2-001"]()
    mom = ts_.target_position(bars, ts_.params()).to_numpy()
    rev = rsi.target_position(bars, rsi.params()).to_numpy()
    p, idx = pos.to_numpy(), np.arange(len(bars)) >= 300
    assert (p[idx & calm] == mom[idx & calm]).all()
    assert (p[idx & ~calm] == rev[idx & ~calm]).all()
    short = _run("ML-HMM-001", bars.iloc[:250].copy())
    assert (short == 0).all()  # not enough data for a model


def test_round_number_fade_shorts_a_rejected_first_touch():
    ts = pd.date_range("2025-03-03", periods=120, freq="1min", tz="UTC")
    close = np.concatenate([np.linspace(1.0900, 1.0990, 100),
                            [1.0995, 1.0996, 1.0990, 1.0985, 1.0980, 1.0975] + [1.0975] * 14])
    bars = _frame(ts, close)
    i = 102
    bars.loc[i, "high"] = 1.1003  # pokes through 1.1000, closes back below
    pos = _run("SC-ROUND-001", bars)
    assert pos.iloc[i] == -1 and (pos.iloc[:i] == 0).all()
    # target 0.2 x 0.01 below the 1.0990 close = 1.0970 is not reached: the 5-bar timer ends it
    assert (pos.iloc[i:i + 5] == -1).all() and pos.iloc[i + 5] == 0
    # a second touch within `memory` bars is not a first touch
    bars.loc[i + 8, "high"] = 1.1001
    assert _run("SC-ROUND-001", bars).iloc[i + 8] == 0
