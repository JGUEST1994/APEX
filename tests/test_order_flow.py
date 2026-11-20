"""Optional buy volume on bars (aggressive buyers) and the H28 delta-divergence strategy."""

import httpx
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from apex.api.app import create_app
from apex.core.config import Settings
from apex.db.session import make_engine, make_session_factory
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.base import normalise_bars
from apex.marketdata.calendars import get_calendar
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.binance import BinanceSpotProvider
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.marketdata.repository import (
    frame_sha256,
    get_instrument,
    load_bars,
    store_bars,
    upsert_instrument,
)
from apex.marketdata.resample import resample_bars
from apex.marketdata.validation import validate_bars
from apex.strategies.builtin6 import cumulative_delta
from apex.strategies.registry import all_strategies
from tests.conftest import utc

BTC = by_symbol()["BTCUSDT"]


def test_binance_keeps_taker_buy_volume():
    def handler(_req):
        k = [1_740_000_000_000, "100", "101", "99", "100.5", "12.5", 0, "0", 9, "7.25", "0", "0"]
        return httpx.Response(200, json=[k])

    p = BinanceSpotProvider(client=httpx.Client(base_url="https://x",
                                                transport=httpx.MockTransport(handler)))
    df = p.fetch_bars(BTC, Timeframe.M1, utc(2025, 2, 19), utc(2025, 2, 21))
    assert df["buy_volume"].tolist() == [7.25] and df["volume"].tolist() == [12.5]


def test_frames_without_buy_volume_are_unchanged():
    raw = pd.DataFrame({"ts": [utc(2025, 1, 1)], "open": [1], "high": [2], "low": [0.5],
                        "close": [1.5], "volume": [10]})
    df = normalise_bars(raw)
    assert "buy_volume" not in df.columns
    assert frame_sha256(df) == frame_sha256(normalise_bars(raw.assign(extra=1)))
    with_buy = normalise_bars(raw.assign(buy_volume=["4"]))
    assert with_buy["buy_volume"].tolist() == [4.0]
    assert frame_sha256(with_buy) != frame_sha256(df)


def test_validation_rejects_buy_volume_outside_volume():
    df = SyntheticProvider(order_flow=True).fetch_bars(BTC, Timeframe.H1, utc(2025, 1, 1),
                                                       utc(2025, 1, 2))
    assert validate_bars(df, BTC, Timeframe.H1).issues == []
    df.loc[3, "buy_volume"] = df.loc[3, "volume"] * 2
    df.loc[5, "buy_volume"] = -1.0
    rep = validate_bars(df, BTC, Timeframe.H1)
    assert rep.count("buy_volume_out_of_range") == 2 and len(rep.clean) == len(df) - 2


def test_store_and_load_round_trip(session):
    inst = upsert_instrument(session, BTC)
    df = SyntheticProvider(order_flow=True).fetch_bars(BTC, Timeframe.H1, utc(2025, 1, 1),
                                                       utc(2025, 1, 2))
    store_bars(session, inst.id, Timeframe.H1, "flow", df)
    store_bars(session, inst.id, Timeframe.H1, "plain", df.drop(columns="buy_volume"))
    session.commit()
    back = load_bars(session, inst.id, Timeframe.H1, "flow")
    np.testing.assert_allclose(back["buy_volume"], df["buy_volume"])
    assert frame_sha256(back) == frame_sha256(df)
    plain = load_bars(session, inst.id, Timeframe.H1, "plain")
    assert "buy_volume" not in plain.columns  # old/other sources: same frame and hash as before
    assert frame_sha256(plain) == frame_sha256(df.drop(columns="buy_volume"))


def test_mixed_history_loads_unknown_buy_volume_as_nan(session):
    inst = upsert_instrument(session, BTC)
    df = SyntheticProvider(order_flow=True).fetch_bars(BTC, Timeframe.H1, utc(2025, 1, 1),
                                                       utc(2025, 1, 2))
    store_bars(session, inst.id, Timeframe.H1, "mix", df.iloc[:10].drop(columns="buy_volume"))
    store_bars(session, inst.id, Timeframe.H1, "mix", df.iloc[10:])
    session.commit()
    back = load_bars(session, inst.id, Timeframe.H1, "mix")
    assert back["buy_volume"].iloc[:10].isna().all() and back["buy_volume"].iloc[10:].notna().all()


def test_resample_sums_buy_volume_only_when_complete():
    df = SyntheticProvider(order_flow=True).fetch_bars(BTC, Timeframe.M15, utc(2025, 1, 1),
                                                       utc(2025, 1, 1, 3))
    df.loc[5, "buy_volume"] = np.nan  # inside the second hour
    out = resample_bars(df, Timeframe.M15, Timeframe.H1, get_calendar(BTC.calendar))
    assert len(out) == 3
    assert out["buy_volume"].iloc[0] == df["buy_volume"].iloc[:4].sum()
    assert np.isnan(out["buy_volume"].iloc[1]) and not np.isnan(out["buy_volume"].iloc[2])
    plain = resample_bars(df.drop(columns="buy_volume"), Timeframe.M15, Timeframe.H1,
                          get_calendar(BTC.calendar))
    assert "buy_volume" not in plain.columns


def test_api_returns_null_for_unknown_buy_volume(db_url, session):
    inst = upsert_instrument(session, BTC)
    df = SyntheticProvider(order_flow=True).fetch_bars(BTC, Timeframe.H1, utc(2025, 1, 1),
                                                       utc(2025, 1, 1, 4))
    store_bars(session, inst.id, Timeframe.H1, "mix", df.iloc[:2].drop(columns="buy_volume"))
    store_bars(session, inst.id, Timeframe.H1, "mix", df.iloc[2:])
    session.commit()
    engine = make_engine(db_url)
    client = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                                   make_session_factory(engine)))
    rows = client.get("/instruments/BTCUSDT/bars",
                      params={"timeframe": "1h", "source": "mix"}).json()
    assert [r["buy_volume"] is None for r in rows] == [True, True, False, False]
    engine.dispose()


def test_ingest_keeps_buy_volume(session):
    ingest_bars(session, SyntheticProvider(order_flow=True), BTC, Timeframe.H1,
                utc(2025, 1, 1), utc(2025, 1, 2))
    session.commit()
    back = load_bars(session, get_instrument(session, "BTCUSDT").id, Timeframe.H1, "synthetic")
    assert back["buy_volume"].notna().all()


# --- the strategy -------------------------------------------------------------------------


def _flow_bars(n=40):
    """Flat market at 100 with balanced flow, then one bar makes a new high."""
    ts = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    b = pd.DataFrame({"ts": ts, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
                      "volume": 100.0, "buy_volume": 50.0})
    return b


def _run(bars, **over):
    s = all_strategies()["OF-DELTA-001"]()
    return s.target_position(bars, s.params(over))


def test_new_high_without_buyers_sells():
    b = _flow_bars()
    b.loc[25, "buy_volume"] = 80.0  # delta peaks here
    b.loc[30, ["high", "close"]] = 101.0, 100.6  # new high...
    b.loc[30, "buy_volume"] = 20.0  # ...on aggressive selling: delta below its high
    pos = _run(b)
    assert (pos.iloc[:30] == 0).all() and pos.iloc[30] == -1.0


def test_new_high_with_buyers_does_not_sell():
    b = _flow_bars()
    b.loc[30, ["high", "close"]] = 101.0, 100.6
    b.loc[30, "buy_volume"] = 90.0  # buyers did push it: delta makes a new high too
    assert (_run(b) == 0).all()


def test_new_low_without_sellers_buys_unless_long_disabled():
    b = _flow_bars()
    b.loc[25, "buy_volume"] = 20.0  # delta trough here
    b.loc[30, ["low", "close"]] = 99.0, 99.4
    b.loc[30, "buy_volume"] = 80.0
    assert _run(b).iloc[30] == 1.0
    assert (_run(b, allow_long=False) == 0).all()


def test_short_exits_at_the_prior_swing_low():
    b = _flow_bars()
    b.loc[25, "buy_volume"] = 80.0
    b.loc[30, ["high", "close"]] = 101.0, 100.6
    b.loc[30, "buy_volume"] = 20.0
    b.loc[33, ["low", "close"]] = 99.0, 99.4  # closes below the prior 20-bar low (99.5)
    pos = _run(b)
    assert (pos.iloc[30:33] == -1.0).all() and pos.iloc[33] == 0.0


def test_never_trades_without_buy_volume():
    b = _flow_bars()
    b.loc[25, "buy_volume"] = 80.0
    b.loc[30, ["high", "close"]] = 101.0, 100.6
    b.loc[30, "buy_volume"] = 20.0
    assert (_run(b.drop(columns="buy_volume")) == 0).all()
    gappy = b.copy()
    gappy.loc[20, "buy_volume"] = np.nan  # unknown flow inside the window: no signal
    assert (_run(gappy) == 0).all()


def test_cumulative_delta():
    b = _flow_bars(3)
    b["buy_volume"] = [60.0, np.nan, 30.0]
    assert cumulative_delta(b).tolist() == [20.0, 20.0, -20.0]


def _absorb(i=30, side=-1):
    """Bar ``i`` makes a new low (side -1) or high (+1) on 5x volume, aggressive flow in the
    breaking direction, and closes where it opened."""
    b = _flow_bars(50)
    b.loc[i, "volume"] = 500.0
    if side < 0:
        b.loc[i, "low"], b.loc[i, "buy_volume"] = 99.0, 100.0  # 400 sold aggressively
    else:
        b.loc[i, "high"], b.loc[i, "buy_volume"] = 101.0, 400.0
    return b


def _run_absorb(bars, **over):
    s = all_strategies()["OF-ABSORB-001"]()
    return s.target_position(bars, s.params(over))


def test_selling_absorbed_at_a_low_buys_then_times_out():
    pos = _run_absorb(_absorb())
    assert (pos.iloc[:30] == 0).all() and (pos.iloc[30:40] == 1.0).all()
    assert pos.iloc[40] == 0.0  # max_bars=10


def test_buying_absorbed_at_a_high_sells():
    pos = _run_absorb(_absorb(side=1))
    assert pos.iloc[30] == -1.0


def test_absorption_needs_heavy_volume_a_stall_and_the_right_flow():
    weak = _absorb()
    weak.loc[30, "volume"], weak.loc[30, "buy_volume"] = 200.0, 50.0
    assert (_run_absorb(weak) == 0).all()
    moved = _absorb()
    moved.loc[30, ["close", "low"]] = 99.2, 99.0  # price did give way: not absorption
    assert (_run_absorb(moved) == 0).all()
    buyers = _absorb()
    buyers.loc[30, "buy_volume"] = 400.0  # buyers drove the bar at the low: nothing absorbed
    assert (_run_absorb(buyers) == 0).all()
    assert (_run_absorb(_absorb().drop(columns="buy_volume")) == 0).all()
