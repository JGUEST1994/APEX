"""H23: earnings dates from SEC EDGAR (mocked HTTP) and the anchored-VWAP reclaim strategy."""

import json

import httpx
import numpy as np
import pandas as pd
import pytest

from apex.cli import main as cli
from apex.core.errors import ProviderError
from apex.marketdata.earnings import SecEarningsDates, anchor_dates
from apex.strategies.builtin6 import anchor_segments
from apex.strategies.registry import all_strategies

_CLIENT = httpx.Client  # kept before any test patches httpx.Client
TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
SUBS = {"filings": {"recent": {
    "form": ["8-K", "10-Q", "8-K", "8-K"],
    "items": ["2.02,9.01", "", "5.02", "2.02"],
    "acceptanceDateTime": ["2024-08-01T16:30:42.000Z", "2024-08-02T18:00:00.000Z",
                           "2024-09-10T08:00:00.000Z", "2024-10-31T07:15:00.000Z"]}}}


def _client(calls):
    def handler(req):
        calls.append(req)
        if req.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=TICKERS)
        assert req.url.path == "/submissions/CIK0000320193.json"
        return httpx.Response(200, json=SUBS)
    return _CLIENT(transport=httpx.MockTransport(handler))


def test_only_item_202_filings_and_anchor_after_the_news():
    calls = []
    rel = SecEarningsDates("Test Person test@example.com", _client(calls)).releases("aapl")
    assert len(rel) == 2 and all(c.headers["User-Agent"].endswith("@example.com")
                                 for c in calls)
    # 16:30 after the close -> next day; 07:15 before the open -> same day
    assert anchor_dates(rel) == ["2024-08-02", "2024-10-31"]


def test_user_agent_is_required_and_unknown_ticker_explains():
    with pytest.raises(ProviderError, match="APEX_SEC_USER_AGENT"):
        SecEarningsDates("")
    with pytest.raises(ProviderError, match="not in the SEC"):
        SecEarningsDates("a b@c.d", _client([])).cik("NOPE")


def _bars(close, start="2024-01-01"):
    close = np.asarray(close, float)
    return pd.DataFrame({"ts": pd.date_range(start, periods=len(close), freq="D", tz="UTC"),
                         "open": close, "high": close + 0.5, "low": close - 0.5,
                         "close": close, "volume": 100.0})


def test_reclaim_of_anchored_vwap_buys_and_close_below_exits():
    # anchor on day 2 at 100; dips to 98, reclaims 101, then breaks down to 97
    close = [100, 100, 100, 99, 98, 101, 102, 103, 97, 97]
    bars = _bars(close)
    s = all_strategies()["VWAP-EARN-001"]()
    pos = s.target_position(bars, s.params({"anchors": ["2024-01-03"], "atr_mult": 50}))
    assert pos.tolist() == [0, 0, 0, 0, 0, 1, 1, 1, 0, 0]
    none = s.target_position(bars, s.params())
    assert (none == 0).all()  # no earnings dates, no trades
    assert anchor_segments(bars["ts"], ["2024-01-03", "2024-01-06"]).tolist() == \
        [0, 0, 1, 1, 1, 2, 2, 2, 2, 2]


def test_cli_saves_dates_and_backtest_reads_them_from_file(tmp_path, monkeypatch, capsys):
    import apex.marketdata.earnings as earn

    monkeypatch.setenv("APEX_SEC_USER_AGENT", "Test Person test@example.com")
    monkeypatch.setattr(earn.httpx, "Client", lambda **kw: _client([]))
    from apex.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert cli(["earnings-dates", "--symbol", "AAPL", "--out", str(tmp_path)]) == 0
        monkeypatch.delenv("APEX_SEC_USER_AGENT")
        get_settings.cache_clear()
        assert cli(["earnings-dates", "--symbol", "AAPL", "--out", str(tmp_path)]) == 1
    finally:
        get_settings.cache_clear()
    saved = tmp_path / "AAPL_earnings.json"
    assert json.loads(saved.read_text()) == ["2024-08-02", "2024-10-31"]
    assert "anchors=@" in capsys.readouterr().out
    from apex.cli import _param

    assert _param("@" + str(saved)) == ["2024-08-02", "2024-10-31"] and _param("3") == 3
