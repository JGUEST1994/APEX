from datetime import date

import httpx
import pandas as pd
import pytest

from apex.cli import main as cli
from apex.core.errors import ProviderError
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.calendars import get_calendar
from apex.marketdata.ingest import update_bars
from apex.marketdata.providers.alpaca import AlpacaStocksProvider
from apex.marketdata.providers.stored import ResampledProvider
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.marketdata.reconcile import reconcile
from apex.marketdata.repository import get_instrument, load_bars
from apex.marketdata.resample import resample_bars
from apex.marketdata.validation import validate_bars
from tests.conftest import utc

SPY, ES, EURUSD, BTC = (by_symbol()[s] for s in ("SPY", "ES", "EURUSD", "BTCUSDT"))


def test_session_dates():
    fx, cme, eq = (get_calendar(n) for n in ("fx_24x5", "cme_globex", "us_equity_rth"))
    assert fx.session_date(utc(2025, 3, 9, 22)) == date(2025, 3, 10)  # Sun 18:00 NY
    assert cme.session_date(utc(2025, 3, 10, 22, 30)) == date(2025, 3, 11)  # Mon 18:30 NY
    assert eq.session_date(utc(2025, 3, 11, 2)) == date(2025, 3, 10)  # Mon 22:00 NY


def test_cme_and_fx_holidays():
    cme, fx = get_calendar("cme_globex"), get_calendar("fx_24x5")
    assert not cme.is_session_day(date(2025, 12, 25))
    assert not cme.is_session_day(date(2025, 4, 18))  # Good Friday
    assert not cme.is_open(utc(2025, 12, 25, 15))
    assert not fx.is_open(utc(2025, 12, 25, 15)) and fx.is_open(utc(2025, 12, 26, 15))
    assert not fx.is_session_day(date(2026, 1, 1))


def test_resample_intraday_matches_direct_aggregation():
    m1 = SyntheticProvider().fetch_bars(SPY, Timeframe.M1, utc(2025, 3, 10), utc(2025, 3, 11))
    h1 = resample_bars(m1, Timeframe.M1, Timeframe.H1, get_calendar("us_equity_rth"))
    # 09:30-16:00 ET = 13:30-20:00 UTC: the 13:00 bucket holds the 30 minutes the market
    # is open, which the calendar counts as complete
    assert list(h1["ts"].dt.hour) == [13, 14, 15, 16, 17, 18, 19]
    assert len(resample_bars(m1.drop(index=range(40, 45)), Timeframe.M1, Timeframe.H1,
                             get_calendar("us_equity_rth"))) == 6  # gap -> partial dropped
    first = m1[(m1["ts"] >= "2025-03-10 14:00Z") & (m1["ts"] < "2025-03-10 15:00Z")]
    row = h1.iloc[1]
    assert row["open"] == first["open"].iloc[0] and row["close"] == first["close"].iloc[-1]
    assert row["high"] == first["high"].max() and row["volume"] == first["volume"].sum()
    assert validate_bars(h1, SPY, Timeframe.H1).errors == []


def test_resample_keep_partial_and_daily_sessions():
    cal = get_calendar("cme_globex")
    m30 = SyntheticProvider().fetch_bars(ES, Timeframe.M30, utc(2025, 3, 9), utc(2025, 3, 12))
    d1 = resample_bars(m30, Timeframe.M30, Timeframe.D1, cal)
    # Sunday-evening session belongs to Monday
    assert [t.date() for t in d1["ts"]][:2] == [date(2025, 3, 10), date(2025, 3, 11)]
    sunday = m30[m30["ts"] < "2025-03-10 00:00Z"]
    assert d1["open"].iloc[0] == sunday["open"].iloc[0]


def test_resample_rejects_bad_direction():
    with pytest.raises(ValueError):
        resample_bars(pd.DataFrame(), Timeframe.H1, Timeframe.M5, get_calendar("crypto_24x7"))
    with pytest.raises(ValueError):
        resample_bars(pd.DataFrame(), Timeframe.M15, Timeframe.M5, None)


def test_reconcile():
    a = SyntheticProvider().fetch_bars(BTC, Timeframe.D1, utc(2025, 1, 1), utc(2025, 1, 11))
    b = a.copy()
    b.loc[3, "close"] *= 1.01
    b = b.drop(index=5)
    rep = reconcile(a, b, tolerance_bps=5)
    assert rep.compared == 9 and len(rep.only_in_a) == 1 and not rep.only_in_b
    assert len(rep.mismatches) == 1 and rep.mismatches["diff_bps"].iloc[0] > 90
    assert not rep.ok and reconcile(a, a).ok


def test_update_bars_incremental(session):
    p = SyntheticProvider()
    r1 = update_bars(session, p, BTC, Timeframe.H1, backfill_start=utc(2025, 1, 1),
                     now=utc(2025, 1, 2, 0, 30))
    assert r1.stored == 24  # forming 00:00 bar excluded
    assert update_bars(session, p, BTC, Timeframe.H1, backfill_start=utc(2025, 1, 1),
                       now=utc(2025, 1, 2, 0, 59)) is None
    r2 = update_bars(session, p, BTC, Timeframe.H1, backfill_start=utc(2025, 1, 1),
                     now=utc(2025, 1, 2, 3, 5))
    assert r2.stored == 3
    session.commit()


def test_resampled_provider_through_pipeline(session):
    from apex.marketdata.ingest import ingest_bars

    ingest_bars(session, SyntheticProvider(), BTC, Timeframe.M15, utc(2025, 1, 1), utc(2025, 1, 2))
    prov = ResampledProvider(session, "synthetic", Timeframe.M15)
    r = ingest_bars(session, prov, BTC, Timeframe.H1, utc(2025, 1, 1), utc(2025, 1, 2))
    session.commit()
    assert r.status == "ok" and r.stored == 24 and prov.name == "synthetic:rs15m"
    inst = get_instrument(session, "BTCUSDT")
    assert len(load_bars(session, inst.id, Timeframe.H1, "synthetic:rs15m")) == 24


def _alpaca_client(pages):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        tok = req.url.params.get("page_token")
        return httpx.Response(200, json=pages[int(tok) if tok else 0])

    return httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler)), calls


def test_alpaca_paginates_and_normalises_daily():
    pages = [
        {"bars": [{"t": "2025-03-10T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}],
         "next_page_token": "1"},
        {"bars": [{"t": "2025-03-11T04:00:00Z", "o": 1.5, "h": 2, "l": 1, "c": 1.8, "v": 12}],
         "next_page_token": None},
    ]
    client, calls = _alpaca_client(pages)
    p = AlpacaStocksProvider("kid", "sec", client=client)
    df = p.fetch_bars(SPY, Timeframe.D1, utc(2025, 3, 10), utc(2025, 3, 12))
    assert list(df["ts"]) == [pd.Timestamp("2025-03-10", tz="UTC"), pd.Timestamp("2025-03-11", tz="UTC")]
    assert len(calls) == 2 and calls[0].headers["APCA-API-KEY-ID"] == "kid"
    assert calls[0].url.params["feed"] == "iex"


def test_alpaca_errors_do_not_leak_secret():
    client = httpx.Client(base_url="https://x",
                          transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(ProviderError) as ei:
        AlpacaStocksProvider("kid", "TOPSECRET", client=client).fetch_bars(
            SPY, Timeframe.D1, utc(2025, 3, 10), utc(2025, 3, 12))
    assert "TOPSECRET" not in str(ei.value)
    with pytest.raises(ProviderError):
        AlpacaStocksProvider("", "")
    assert not AlpacaStocksProvider("k", "s", client=client).supports(BTC, Timeframe.D1)


def test_cli_update_resample_reconcile(db_url, monkeypatch, capsys):
    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    from apex.core.config import get_settings
    get_settings.cache_clear()
    try:
        assert cli(["update", "--provider", "synthetic", "--symbols", "BTCUSDT,NOPE",
                    "--timeframe", "1h", "--backfill-start", "2025-01-01"]) == 2
        assert cli(["ingest", "--provider", "synthetic", "--symbol", "BTCUSDT", "--timeframe",
                    "15m", "--start", "2025-01-01", "--end", "2025-01-03"]) == 0
        assert cli(["resample", "--symbol", "BTCUSDT", "--source", "synthetic",
                    "--from-timeframe", "15m", "--to-timeframe", "1h",
                    "--start", "2025-01-01", "--end", "2025-01-03"]) == 0
        rc = cli(["reconcile", "--symbol", "BTCUSDT", "--timeframe", "1h",
                  "--sources", "synthetic,synthetic:rs15m", "--start", "2025-01-01",
                  "--end", "2025-01-03"])
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "compared=48" in out
    assert rc == 1  # independent synthetic series differ -> mismatches reported
