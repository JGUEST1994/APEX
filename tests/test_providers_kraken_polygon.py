"""Kraken and Polygon adapters, tested with mocked HTTP (the sandbox cannot reach them)."""

import httpx
import pandas as pd
import pytest

from apex.core.errors import ProviderError
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.kraken import KrakenSpotProvider, kraken_pair
from apex.marketdata.providers.polygon import PolygonStocksProvider
from tests.conftest import utc

BTC, SPY = by_symbol()["BTCUSDT"], by_symbol()["SPY"]


def _client(handler):
    calls = []

    def wrap(req):
        calls.append(req)
        return handler(req, len(calls))
    return httpx.Client(base_url="https://x", transport=httpx.MockTransport(wrap)), calls


def _k(ts, c):
    return [ts, str(c), str(c + 1), str(c - 1), str(c), str(c), "2.5", 7]


def test_kraken_pages_until_end_and_maps_pair():
    t0 = int(utc(2025, 1, 1).timestamp())

    def handler(req, n):
        if n == 1:
            rows, last = [_k(t0, 100), _k(t0 + 3600, 101)], t0 + 3600
        elif n == 2:  # overlapping row is dropped, new one kept
            rows, last = [_k(t0 + 3600, 101), _k(t0 + 7200, 102)], t0 + 7200
        else:
            rows, last = [], t0 + 7200
        return httpx.Response(200, json={"error": [], "result": {"XBTUSDT": rows,
                                                                 "last": last}})
    client, calls = _client(handler)
    df = KrakenSpotProvider(client=client).fetch_bars(BTC, Timeframe.H1, utc(2025, 1, 1),
                                                      utc(2025, 1, 2))
    assert kraken_pair(BTC) == "XBTUSDT"
    assert calls[0].url.params["pair"] == "XBTUSDT" and calls[0].url.params["interval"] == "60"
    assert list(df["close"]) == [100.0, 101.0, 102.0]
    assert df["ts"].iloc[0] == pd.Timestamp("2025-01-01", tz="UTC")
    assert df["volume"].iloc[0] == 2.5


def test_kraken_reports_api_errors_and_scope():
    client, _ = _client(lambda r, n: httpx.Response(200, json={"error": ["EQuery:Unknown"]}))
    with pytest.raises(ProviderError, match="EQuery"):
        KrakenSpotProvider(client=client).fetch_bars(BTC, Timeframe.H1, utc(2025, 1, 1),
                                                     utc(2025, 1, 2))
    assert not KrakenSpotProvider(client=client).supports(SPY, Timeframe.D1)


def test_polygon_follows_next_url_and_stamps_daily_bars():
    ms = int(pd.Timestamp("2025-03-10 04:00", tz="UTC").timestamp() * 1000)

    def handler(req, n):
        if n == 1:
            return httpx.Response(200, json={"results": [
                {"t": ms, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}],
                "next_url": "https://x/v2/aggs/cursor/abc"})
        return httpx.Response(200, json={"results": [
            {"t": ms + 86_400_000, "o": 1.5, "h": 2, "l": 1, "c": 1.8, "v": 12}]})
    client, calls = _client(handler)
    df = PolygonStocksProvider("KEY", client=client).fetch_bars(
        SPY, Timeframe.D1, utc(2025, 3, 10), utc(2025, 3, 12))
    assert list(df["ts"]) == [pd.Timestamp("2025-03-10", tz="UTC"),
                              pd.Timestamp("2025-03-11", tz="UTC")]
    assert calls[0].url.path.startswith("/v2/aggs/ticker/SPY/range/1/day/")
    assert all(c.url.params["apiKey"] == "KEY" for c in calls)
    assert calls[1].url.path == "/v2/aggs/cursor/abc"


def test_polygon_errors_do_not_leak_key():
    client, _ = _client(lambda r, n: httpx.Response(401))
    with pytest.raises(ProviderError) as ei:
        PolygonStocksProvider("TOPSECRET", client=client).fetch_bars(
            SPY, Timeframe.D1, utc(2025, 3, 10), utc(2025, 3, 12))
    assert "TOPSECRET" not in str(ei.value)
    with pytest.raises(ProviderError):
        PolygonStocksProvider("")
    assert not PolygonStocksProvider("k", client=client).supports(BTC, Timeframe.D1)
