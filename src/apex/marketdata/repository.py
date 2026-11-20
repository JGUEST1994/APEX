"""Persistence for instruments and bars. Idempotent: re-ingesting the same data is a no-op."""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from apex.core.errors import UnknownInstrumentError
from apex.db.models import Instrument, PriceBar
from apex.domain.market import AssetClass, CostModel, InstrumentSpec, Timeframe
from apex.marketdata.base import BAR_COLUMNS, OPTIONAL_COLUMNS


def upsert_instrument(session: Session, spec: InstrumentSpec) -> Instrument:
    row = session.scalar(
        select(Instrument).where(Instrument.symbol == spec.symbol, Instrument.venue == spec.venue)
    )
    if row is None:
        row = Instrument(symbol=spec.symbol, venue=spec.venue)
        session.add(row)
    row.asset_class = spec.asset_class.value
    row.calendar = spec.calendar
    row.base_currency = spec.base_currency
    row.quote_currency = spec.quote_currency
    row.tick_size = spec.tick_size
    row.contract_multiplier = spec.contract_multiplier
    row.lot_size = spec.lot_size
    row.cost_model = dataclasses.asdict(spec.costs)
    session.flush()
    return row


def get_instrument(session: Session, symbol: str, venue: str | None = None) -> Instrument:
    q = select(Instrument).where(Instrument.symbol == symbol)
    if venue:
        q = q.where(Instrument.venue == venue)
    rows = session.scalars(q).all()
    if len(rows) != 1:
        raise UnknownInstrumentError(
            f"{symbol}: {'not found' if not rows else 'ambiguous, specify venue'}")
    return rows[0]


def to_spec(row: Instrument) -> InstrumentSpec:
    return InstrumentSpec(
        symbol=row.symbol, asset_class=AssetClass(row.asset_class), venue=row.venue,
        calendar=row.calendar, quote_currency=row.quote_currency,
        base_currency=row.base_currency, tick_size=row.tick_size,
        contract_multiplier=row.contract_multiplier, lot_size=row.lot_size,
        costs=CostModel(**(row.cost_model or {})),
    )


def store_bars(session: Session, instrument_id: int, timeframe: Timeframe, source: str,
               df: pd.DataFrame, ingestion_run_id: int | None = None) -> int:
    """Insert bars, ignoring rows that already exist. Returns number of rows inserted."""
    if df.empty:
        return 0
    has_buy = "buy_volume" in df.columns
    records = [
        {"instrument_id": instrument_id, "timeframe": timeframe.value, "source": source,
         "ingestion_run_id": ingestion_run_id, "ts": r.ts.to_pydatetime(), "open": r.open,
         "high": r.high, "low": r.low, "close": r.close, "volume": r.volume,
         "buy_volume": _opt(r.buy_volume) if has_buy else None}
        for r in df.itertuples(index=False)
    ]
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    inserted = 0
    for i in range(0, len(records), 2000):  # stays under SQLite bind-parameter limit
        stmt = (insert(PriceBar).values(records[i : i + 2000]).on_conflict_do_nothing()
                .returning(PriceBar.ts))
        inserted += len(session.execute(stmt).all())
    return inserted


def load_bars(session: Session, instrument_id: int, timeframe: Timeframe, source: str,
              start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
    q = select(PriceBar).where(PriceBar.instrument_id == instrument_id,
                               PriceBar.timeframe == timeframe.value, PriceBar.source == source)
    if start:
        q = q.where(PriceBar.ts >= start)
    if end:
        q = q.where(PriceBar.ts < end)
    rows = session.scalars(q.order_by(PriceBar.ts)).all()
    cols = BAR_COLUMNS + [c for c in OPTIONAL_COLUMNS
                          if any(getattr(r, c) is not None for r in rows)]
    df = pd.DataFrame([{c: getattr(r, c) for c in cols} for r in rows], columns=cols)
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        for c in cols[len(BAR_COLUMNS):]:
            df[c] = df[c].astype("float64")  # bars stored before the source supplied it: NaN
    return df


def _opt(v: float) -> float | None:
    return None if pd.isna(v) else float(v)


def latest_bar_ts(session: Session, instrument_id: int, timeframe: Timeframe,
                  source: str) -> datetime | None:
    return session.scalar(
        select(func.max(PriceBar.ts)).where(PriceBar.instrument_id == instrument_id,
                                            PriceBar.timeframe == timeframe.value,
                                            PriceBar.source == source))


def frame_sha256(df: pd.DataFrame) -> str:
    """Content hash of a canonical bar frame: the data version used by experiments.

    Optional columns count only when present, so frames without them hash exactly as they
    did before those columns existed."""
    canon = df[BAR_COLUMNS + [c for c in OPTIONAL_COLUMNS if c in df.columns]].copy()
    canon["ts"] = canon["ts"].map(lambda t: t.isoformat())
    payload = canon.to_csv(index=False, float_format="%.10g", lineterminator="\n")
    return hashlib.sha256(payload.encode()).hexdigest()


def dataset_sha256(session: Session, instrument_id: int, timeframe: Timeframe, source: str,
                   start: datetime | None = None, end: datetime | None = None) -> str:
    return frame_sha256(load_bars(session, instrument_id, timeframe, source, start, end))
