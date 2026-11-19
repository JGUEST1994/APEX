import pandas as pd
from sqlalchemy import inspect, select

from apex.core.errors import ProviderError
from apex.db.models import DataQualityIssue, IngestionRun
from apex.domain.instruments import DEFAULT_UNIVERSE, by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.base import MarketDataProvider
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.marketdata.repository import (
    dataset_sha256,
    get_instrument,
    latest_bar_ts,
    load_bars,
    to_spec,
    upsert_instrument,
)
from tests.conftest import utc

ES = by_symbol()["ES"]


def test_migrations_create_schema(session):
    tables = set(inspect(session.get_bind()).get_table_names())
    assert {"instruments", "bars", "ingestion_runs", "data_quality_issues", "strategies",
            "strategy_versions", "experiments", "research_documents", "research_chunks",
            "research_journal", "audit_log", "alembic_version", "paper_accounts", "paper_orders",
            "paper_fills", "paper_positions"} <= tables


def test_instrument_roundtrip(session):
    for spec in DEFAULT_UNIVERSE:
        upsert_instrument(session, spec)
    upsert_instrument(session, ES)  # idempotent
    session.commit()
    assert to_spec(get_instrument(session, "ES")) == ES


def test_ingest_is_idempotent_and_versioned(session):
    p = SyntheticProvider()
    start, end = utc(2025, 3, 3), utc(2025, 3, 8)
    r1 = ingest_bars(session, p, ES, Timeframe.H1, start, end)
    session.commit()
    assert r1.status == "ok" and r1.stored == r1.received > 0
    r2 = ingest_bars(session, p, ES, Timeframe.H1, start, end)
    session.commit()
    assert r2.stored == 0 and r2.received == r1.received
    runs = session.scalars(select(IngestionRun).order_by(IngestionRun.id)).all()
    assert runs[0].content_sha256 == runs[1].content_sha256

    inst_id = runs[0].instrument_id
    df = load_bars(session, inst_id, Timeframe.H1, "synthetic")
    assert len(df) == r1.received
    assert df["ts"].iloc[0].tzinfo is not None
    assert dataset_sha256(session, inst_id, Timeframe.H1, "synthetic") == runs[0].content_sha256
    assert latest_bar_ts(session, inst_id, Timeframe.H1, "synthetic") == df["ts"].iloc[-1]


class _Dirty(MarketDataProvider):
    name = "dirty"

    def supports(self, spec, timeframe):
        return True

    def _fetch(self, spec, timeframe, start, end):
        t = [utc(2025, 3, 4, 15), utc(2025, 3, 4, 16), utc(2025, 3, 4, 18)]
        return pd.DataFrame({"ts": t, "open": [1, 1, 1], "high": [2, 0.5, 2],
                             "low": [0.5, 0.4, 0.5], "close": [1.5, 1, 1.2]})


def test_ingest_records_quality_issues(session):
    r = ingest_bars(session, _Dirty(), ES, Timeframe.H1, utc(2025, 3, 4, 15), utc(2025, 3, 4, 19))
    session.commit()
    assert r.status == "warn" and r.stored == 2
    kinds = {i.kind for i in session.scalars(select(DataQualityIssue))}
    assert {"ohlc_inconsistent", "gap"} <= kinds


class _Broken(MarketDataProvider):
    name = "broken"

    def supports(self, spec, timeframe):
        return True

    def _fetch(self, *a):
        raise ProviderError("upstream down")


def test_ingest_failure_is_recorded(session):
    r = ingest_bars(session, _Broken(), ES, Timeframe.H1, utc(2025, 3, 4), utc(2025, 3, 5))
    session.commit()
    run = session.get(IngestionRun, r.run_id)
    assert r.status == run.status == "failed" and "upstream down" in run.error


def test_downgrade_roundtrip(db_url):
    from apex.db.migrate import downgrade, upgrade
    downgrade(db_url, "base")
    upgrade(db_url)


def test_large_ingest_batches(session):
    btc = by_symbol()["BTCUSDT"]
    r = ingest_bars(session, SyntheticProvider(), btc, Timeframe.M1, utc(2025, 1, 1), utc(2025, 1, 4))
    session.commit()
    assert r.received == r.stored == 3 * 24 * 60
