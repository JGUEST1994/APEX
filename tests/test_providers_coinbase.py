"""Coinbase adapter, tested with mocked HTTP (the sandbox cannot reach it)."""

from datetime import datetime, timedelta

import httpx
import pandas as pd
import pytest

from apex.core.errors import ProviderError
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.coinbase import CoinbaseSpotProvider, coinbase_product
from tests.conftest import utc

BTC, SPY = by_symbol()["BTCUSDT"], by_symbol()["SPY"]


def _client(handler):
    calls = []

    def wrap(req):
        calls.append(req)
        return handler(req)
    return httpx.Client(base_url="https://x", transport=httpx.MockTransport(wrap)), calls


def _candles(req):
    """Every hourly candle in the requested [start, end], newest first, like Coinbase."""
    start = datetime.fromisoformat(req.url.params["start"])
    end = datetime.fromisoformat(req.url.params["end"])
    t, out = start, []
    while t <= end:
        c = 100 + (t - utc(2025, 1, 1)).total_seconds() / 3600
        out.append([int(t.timestamp()), c - 1, c + 1, c, c + 0.5, 3.0])
        t += timedelta(hours=1)
    return httpx.Response(200, json=out[::-1])


def test_coinbase_walks_300_candle_windows_and_sorts_oldest_first():
    client, calls = _client(_candles)
    p = CoinbaseSpotProvider(client=client, pause=0)
    df = p.fetch_bars(BTC, Timeframe.H1, utc(2025, 1, 1), utc(2025, 2, 1))  # 744 hours
    assert coinbase_product(BTC) == "BTC-USDT"
    assert calls[0].url.path == "/products/BTC-USDT/candles"
    assert calls[0].url.params["granularity"] == "3600"
    assert len(calls) == 3  # 300 + 300 + 144
    assert len(df) == 744 and df["ts"].is_monotonic_increasing and df["ts"].is_unique
    assert df["ts"].iloc[0] == pd.Timestamp("2025-01-01", tz="UTC")
    assert df["ts"].iloc[-1] == pd.Timestamp("2025-01-31 23:00", tz="UTC")
    first = df.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"]) == (100, 101, 99, 100.5)


def test_coinbase_drops_the_candle_still_forming():
    now = pd.Timestamp.now(tz="UTC").floor("h")
    client, _ = _client(_candles)
    df = CoinbaseSpotProvider(client=client, pause=0).fetch_bars(
        BTC, Timeframe.H1, (now - pd.Timedelta(hours=3)).to_pydatetime(),
        (now + pd.Timedelta(hours=1)).to_pydatetime())
    assert df["ts"].max() == now - pd.Timedelta(hours=1)


def test_coinbase_reports_errors_and_scope():
    client, _ = _client(lambda r: httpx.Response(200, json={"message": "NotFound"}))
    with pytest.raises(ProviderError, match="NotFound"):
        CoinbaseSpotProvider(client=client, pause=0).fetch_bars(
            BTC, Timeframe.H1, utc(2025, 1, 1), utc(2025, 1, 2))
    client, _ = _client(lambda r: httpx.Response(429))
    with pytest.raises(ProviderError, match="HTTPStatusError"):
        CoinbaseSpotProvider(client=client, pause=0).fetch_bars(
            BTC, Timeframe.H1, utc(2025, 1, 1), utc(2025, 1, 2))
    p = CoinbaseSpotProvider(client=client)
    assert not p.supports(SPY, Timeframe.D1) and not p.supports(BTC, Timeframe.M30)
    assert p.supports(BTC, Timeframe.M5)
