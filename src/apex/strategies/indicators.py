"""Causal technical indicators. Every value at row t uses rows <= t only."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex.marketdata.calendars import get_calendar


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0).where(up.notna())


def true_range(bars: pd.DataFrame) -> pd.Series:
    prev = bars["close"].shift(1)
    return pd.concat([bars["high"] - bars["low"], (bars["high"] - prev).abs(),
                      (bars["low"] - prev).abs()], axis=1).max(axis=1)


def atr(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(bars).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def donchian(bars: pd.DataFrame, n: int) -> tuple[pd.Series, pd.Series]:
    """Highest high / lowest low of the PREVIOUS n bars (excludes the current bar)."""
    return (bars["high"].shift(1).rolling(n, min_periods=n).max(),
            bars["low"].shift(1).rolling(n, min_periods=n).min())


def session_keys(bars: pd.DataFrame, calendar: str) -> pd.Series:
    cal = get_calendar(calendar)
    return bars["ts"].map(lambda t: cal.session_date(t.to_pydatetime()))


def session_vwap(bars: pd.DataFrame, calendar: str) -> pd.Series:
    key = session_keys(bars, calendar)
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    pv = (typical * bars["volume"]).groupby(key).cumsum()
    vol = bars["volume"].groupby(key).cumsum()
    return pv / vol.replace(0, np.nan)


def hold_until(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """State machine: +1/-1 from ``entry`` (nonzero values) held until ``exit_`` is True.

    Causal: the state at t depends on entry/exit at <= t only.
    """
    out = np.zeros(len(entry))
    state = 0.0
    e, x = entry.to_numpy(), exit_.to_numpy()
    for i in range(len(entry)):
        if state != 0 and x[i]:
            state = 0.0
        if state == 0 and e[i] != 0 and not np.isnan(e[i]):
            state = float(np.sign(e[i]))
        out[i] = state
    return pd.Series(out, index=entry.index)
