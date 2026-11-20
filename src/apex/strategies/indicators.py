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


def bars_left_in_session(bars: pd.DataFrame, calendar: str, cap: int = 10_000) -> pd.Series:
    """How many more bars the calendar schedules in this bar's session after this one.

    Uses only the trading calendar (known in advance) and the bar spacing seen so far, never
    later bars, so it is causal. 0 means this is the session's last bar. Bars the calendar
    does not consider open, and daily or slower bars, get ``cap`` (no session close to plan
    for).
    """
    cal = get_calendar(calendar)
    ts = pd.DatetimeIndex(bars["ts"]).as_unit("ns")
    diffs = np.diff(ts.asi8, prepend=ts.asi8[:1])
    diffs = np.where(diffs > 0, diffs, np.iinfo(np.int64).max)
    step_ns = np.minimum.accumulate(diffs)  # smallest spacing seen up to each bar
    out = np.full(len(ts), cap, dtype=np.int64)
    ends: dict[tuple, pd.Timestamp] = {}
    day = pd.Timedelta(days=1).value
    for i, t in enumerate(ts):
        st = int(step_ns[i])
        if st >= day:
            continue
        py = t.to_pydatetime()
        if not cal.is_open(py):
            continue
        sess = cal.session_date(py)
        end = ends.get((sess, st))
        if end is None:
            step, end, k = pd.Timedelta(st), t, 0
            while k < cap:
                nxt = (end + step).to_pydatetime()
                if not cal.is_open(nxt) or cal.session_date(nxt) != sess:
                    break
                end, k = end + step, k + 1
            ends[(sess, st)] = end
        out[i] = min(cap, max(0, (end - t).value // st))
    return pd.Series(out, index=bars.index)


def flat_before_close(pos: pd.Series, bars: pd.DataFrame, calendar: str,
                      flat_bars: int) -> pd.Series:
    """Zero the target from the bar where ``flat_bars`` bars remain, so with next-bar-open
    fills nothing is held over the session's last ``flat_bars`` bars or overnight."""
    if flat_bars <= 0:
        return pos
    left = bars_left_in_session(bars, calendar)
    return pos.where(left > flat_bars, 0.0)


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


def volume_profile(bars: pd.DataFrame, bins: int = 30, value_area: float = 0.7):
    """Approximate volume-at-price for a block of bars: each bar's volume is spread evenly
    over its high-low range (a bar with no range puts it all at its close).

    This is a stand-in for a tick-built profile. Returns ``(poc, val, vah)``: the point of
    control (centre of the busiest price bin) and the value area low/high (bins added
    outward from the POC, the busier neighbour first, until ``value_area`` of the volume
    is covered). NaNs if the block has no volume.
    """
    lo_px, hi_px = float(bars["low"].min()), float(bars["high"].max())
    vol = bars["volume"].to_numpy(float)
    if not np.isfinite(lo_px) or vol.sum() <= 0:
        return np.nan, np.nan, np.nan
    if hi_px <= lo_px:
        return lo_px, lo_px, hi_px
    edges = np.linspace(lo_px, hi_px, bins + 1)
    h, low, c = (bars[x].to_numpy(float) for x in ("high", "low", "close"))
    span = h - low
    overlap = np.clip(np.minimum(h[:, None], edges[None, 1:])
                      - np.maximum(low[:, None], edges[None, :-1]), 0, None)
    share = np.where(span[:, None] > 0, overlap / np.where(span > 0, span, 1)[:, None], 0.0)
    flat = span <= 0
    if flat.any():
        idx = np.clip(np.searchsorted(edges, c[flat], side="right") - 1, 0, bins - 1)
        share[np.flatnonzero(flat), idx] = 1.0
    prof = (share * vol[:, None]).sum(axis=0)
    p = int(prof.argmax())
    a, b, covered = p, p, prof[p]
    target = value_area * prof.sum()
    while covered < target and (a > 0 or b < bins - 1):
        down = prof[a - 1] if a > 0 else -1.0
        up = prof[b + 1] if b < bins - 1 else -1.0
        if up >= down:
            b += 1
            covered += up
        else:
            a -= 1
            covered += down
    centre = (edges[:-1] + edges[1:]) / 2
    return float(centre[p]), float(edges[a]), float(edges[b + 1])


def prior_session_profile(bars: pd.DataFrame, calendar: str, bins: int = 30,
                          value_area: float = 0.7) -> pd.DataFrame:
    """Per bar, the previous complete session's ``poc``, ``val`` and ``vah`` (NaN in the
    first session). Causal: a session's profile is only used from the next session on."""
    key = session_keys(bars, calendar)
    rows = {k: volume_profile(g, bins, value_area) for k, g in bars.groupby(key, sort=True)}
    prof = pd.DataFrame.from_dict(rows, orient="index", columns=["poc", "val", "vah"])
    prev = prof.shift(1)
    return pd.DataFrame({c: key.map(prev[c]) for c in prev.columns}, index=bars.index)
