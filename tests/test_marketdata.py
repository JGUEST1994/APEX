import httpx
import pandas as pd
import pytest

from apex.core.errors import ProviderError
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.base import normalise_bars
from apex.marketdata.calendars import get_calendar
from apex.marketdata.providers.binance import BinanceSpotProvider
from apex.marketdata.providers.csv_file import CsvFileProvider
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.marketdata.validation import is_stale, validate_bars
from tests.conftest import utc

BTC = by_symbol()["BTCUSDT"]
SPY = by_symbol()["SPY"]


def _frame(rows):
    return normalise_bars(pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]))


def test_synthetic_is_deterministic_and_respects_calendar():
    p = SyntheticProvider()
    a = p.fetch_bars(SPY, Timeframe.H1, utc(2025, 3, 3), utc(2025, 3, 10))
    b = p.fetch_bars(SPY, Timeframe.H1, utc(2025, 3, 3), utc(2025, 3, 10))
    pd.testing.assert_frame_equal(a, b)
    cal = get_calendar("us_equity_rth")
    assert all(cal.is_open(t.to_pydatetime()) for t in a["ts"])
    rep = validate_bars(a, SPY, Timeframe.H1, expected_range=(utc(2025, 3, 3), utc(2025, 3, 10)))
    assert rep.issues == []


def test_normalise_aliases_and_timezones():
    df = normalise_bars(pd.DataFrame({"Date": ["2025-01-02"], "O": [1], "H": [2], "L": [0.5],
                                      "C": [1.5]}))
    assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume"]
    assert str(df["ts"].dt.tz) == "UTC" and df["volume"].iloc[0] == 0


def test_normalise_missing_columns():
    with pytest.raises(ProviderError):
        normalise_bars(pd.DataFrame({"ts": ["2025-01-01"], "close": [1]}))


def test_validation_detects_problems():
    t = [utc(2025, 1, 1, 0, m) for m in range(6)]
    df = _frame([
        (t[0], 10, 11, 9, 10.5, 5),
        (t[1], 10, 11, 9, 10.5, 5),
        (t[1], 10, 11, 9, 10.5, 5),          # exact duplicate -> warning, dropped
        (t[2], 10, 9, 9.5, 10, 5),            # high < low -> error
        (t[3], -1, 11, -2, 10, 5),            # non-positive -> error
        # t[4] missing -> gap
        (t[5], 10, 11, 9, None, 5),           # NaN -> error
        (utc(2025, 1, 1, 0, 6, 30), 10, 11, 9, 10, 5),  # misaligned -> error
    ])
    rep = validate_bars(df, BTC, Timeframe.M1, expected_range=(t[0], utc(2025, 1, 1, 0, 7)))
    kinds = {i.kind for i in rep.issues}
    assert {"duplicate", "ohlc_inconsistent", "non_positive_price", "missing_value",
            "misaligned_timestamp", "gap"} <= kinds
    assert list(rep.clean["ts"]) == [pd.Timestamp(t[0]), pd.Timestamp(t[1])]
    assert not rep.clean["ts"].duplicated().any()


def test_conflicting_duplicate_is_error():
    t = utc(2025, 1, 1)
    df = _frame([(t, 10, 11, 9, 10, 1), (t, 10, 12, 9, 11, 1)])
    rep = validate_bars(df, BTC, Timeframe.M1)
    assert rep.count("duplicate_conflict") == 1 and len(rep.clean) == 1


def test_gap_runs_are_grouped():
    p = SyntheticProvider()
    df = p.fetch_bars(BTC, Timeframe.M1, utc(2025, 1, 1), utc(2025, 1, 1, 1))
    df = df.drop(index=range(10, 20)).reset_index(drop=True)
    rep = validate_bars(df, BTC, Timeframe.M1, expected_range=(utc(2025, 1, 1), utc(2025, 1, 1, 1)))
    gaps = [i for i in rep.issues if i.kind == "gap"]
    assert len(gaps) == 1 and gaps[0].detail.startswith("10 missing")


def test_spike_detected():
    df = SyntheticProvider().fetch_bars(BTC, Timeframe.M1, utc(2025, 1, 1), utc(2025, 1, 1, 2))
    df.loc[60, ["close", "high"]] = df.loc[60, "close"] * 1.5
    assert validate_bars(df, BTC, Timeframe.M1).count("spike") >= 1


def test_staleness():
    cal = get_calendar("us_equity_rth")
    last = utc(2025, 3, 10, 14, 0)
    assert not is_stale(last, Timeframe.M1, utc(2025, 3, 10, 14, 2), cal, 180)
    assert is_stale(last, Timeframe.M1, utc(2025, 3, 10, 14, 5), cal, 180)
    assert not is_stale(last, Timeframe.M1, utc(2025, 3, 10, 22, 0), cal, 180)  # closed
    assert is_stale(None, Timeframe.M1, utc(2025, 3, 10, 14, 5), cal, 180)


def test_csv_provider(tmp_path):
    (tmp_path / "SPY_1d.csv").write_text(
        "Date,Open,High,Low,Close,Volume\n2025-01-03,1,2,0.5,1.5,100\n2025-01-02,1,2,0.5,1.2,90\n")
    p = CsvFileProvider(tmp_path)
    df = p.fetch_bars(SPY, Timeframe.D1, utc(2025, 1, 1), utc(2025, 1, 4))
    assert list(df["close"]) == [1.2, 1.5]
    assert not p.supports(SPY, Timeframe.H1)


def _kline(ms, c):
    return [ms, str(c), str(c + 1), str(c - 1), str(c), "12.5", ms + 59_999, "0", 1, "0", "0", "0"]


def test_binance_paginates_without_network():
    start, end = utc(2025, 1, 1), utc(2025, 1, 1, 0, 30)
    base = int(start.timestamp() * 1000)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(dict(req.url.params))
        cur = int(req.url.params["startTime"])
        idx = -(-(cur - base) // 60_000)  # klines with open time >= startTime
        page = [_kline(base + i * 60_000, 100 + i) for i in range(idx, min(idx + 20, 30))]
        return httpx.Response(200, json=page)

    p = BinanceSpotProvider(client=httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler)))
    p.LIMIT = 20
    df = p.fetch_bars(BTC, Timeframe.M1, start, end)
    assert len(df) == 30 and len(calls) == 2
    assert df["close"].iloc[-1] == 129.0


def test_binance_error_is_provider_error():
    client = httpx.Client(base_url="https://x",
                          transport=httpx.MockTransport(lambda r: httpx.Response(429, json={})))
    with pytest.raises(ProviderError):
        BinanceSpotProvider(client=client).fetch_bars(BTC, Timeframe.M1, utc(2025, 1, 1), utc(2025, 1, 2))


def test_binance_rejects_non_crypto():
    with pytest.raises(ProviderError):
        BinanceSpotProvider(client=httpx.Client()).fetch_bars(SPY, Timeframe.D1, utc(2025, 1, 1), utc(2025, 1, 2))
