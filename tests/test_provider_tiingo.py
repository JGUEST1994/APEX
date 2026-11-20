"""Tiingo adapter, tested with mocked HTTP (the sandbox cannot reach it)."""

import httpx
import pandas as pd
import pytest

from apex.core.errors import ProviderError
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers import tiingo
from apex.marketdata.providers.tiingo import TiingoProvider
from tests.conftest import utc

SPY, EUR, BTC = by_symbol()["SPY"], by_symbol()["EURUSD"], by_symbol()["BTCUSDT"]


def _client(handler):
    calls = []

    def wrap(req):
        calls.append(req)
        return handler(req, len(calls))
    return httpx.Client(base_url="https://x", transport=httpx.MockTransport(wrap)), calls


def test_daily_stocks_use_adjusted_prices_and_key_only_in_header():
    rows = [{"date": "2025-03-10T00:00:00.000Z", "open": 50, "high": 60, "low": 40,
             "close": 55, "volume": 1, "adjOpen": 5, "adjHigh": 6, "adjLow": 4,
             "adjClose": 5.5, "adjVolume": 10},
            {"date": "2025-03-11T00:00:00.000Z", "open": 55, "high": 60, "low": 50,
             "close": 58, "volume": 1, "adjOpen": 5.5, "adjHigh": 6, "adjLow": 5,
             "adjClose": 5.8, "adjVolume": 12}]
    client, calls = _client(lambda r, n: httpx.Response(200, json=rows))
    df = TiingoProvider("KEY", client=client).fetch_bars(SPY, Timeframe.D1, utc(2025, 3, 10),
                                                         utc(2025, 3, 12))
    assert list(df["ts"]) == [utc(2025, 3, 10), utc(2025, 3, 11)]
    assert df["close"].tolist() == [5.5, 5.8] and df["volume"].tolist() == [10, 12]
    assert calls[0].url.path == "/tiingo/daily/spy/prices"
    assert calls[0].headers["Authorization"] == "Token KEY"
    assert "KEY" not in str(calls[0].url)


def test_forex_intraday_walks_windows_without_duplicates(monkeypatch):
    monkeypatch.setattr(tiingo, "ROWS_PER_REQUEST", 2)  # 2 hours per request

    def handler(req, n):
        lo = pd.Timestamp(req.url.params["startDate"], tz="UTC")
        return httpx.Response(200, json=[
            {"date": (lo + pd.Timedelta(hours=h)).isoformat().replace("+00:00", "Z"),
             "ticker": "eurusd", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15}
            for h in range(3)])  # each window also returns its end edge bar
    client, calls = _client(handler)
    df = TiingoProvider("k", client=client).fetch_bars(EUR, Timeframe.H1,
                                                       utc(2025, 3, 10, 8), utc(2025, 3, 10, 14))
    assert len(calls) == 3 and calls[0].url.params["resampleFreq"] == "1hour"
    assert calls[0].url.path == "/tiingo/fx/eurusd/prices"
    assert list(df["ts"]) == [utc(2025, 3, 10, h) for h in range(8, 14)]
    assert (df["volume"] == 0).all()


def test_errors_never_show_the_key_and_unsupported_markets():
    client, _ = _client(lambda r, n: httpx.Response(401))
    with pytest.raises(ProviderError, match="APEX_TIINGO_API_KEY") as ei:
        TiingoProvider("TOPSECRET", client=client).fetch_bars(SPY, Timeframe.D1,
                                                              utc(2025, 3, 10), utc(2025, 3, 12))
    assert "TOPSECRET" not in str(ei.value)
    client, _ = _client(lambda r, n: httpx.Response(200, json={"detail": "Ticker not found"}))
    with pytest.raises(ProviderError, match="not found"):
        TiingoProvider("k", client=client).fetch_bars(SPY, Timeframe.D1, utc(2025, 3, 10),
                                                      utc(2025, 3, 12))
    with pytest.raises(ProviderError):
        TiingoProvider("")
    p = TiingoProvider("k", client=client)
    assert not p.supports(BTC, Timeframe.D1) and not p.supports(SPY, Timeframe.H1)
    assert not p.supports(EUR, Timeframe.D1) and p.supports(EUR, Timeframe.M5)


def test_cli_offers_tiingo_and_asks_for_the_key(monkeypatch):
    from apex.cli import PROVIDERS, make_provider
    from apex.core.config import Settings

    assert "tiingo" in PROVIDERS
    with pytest.raises(SystemExit, match="APEX_TIINGO_API_KEY"):
        make_provider("tiingo", Settings(_env_file=None))
