"""Historical ingestion pipeline: fetch -> normalise -> validate -> store -> record.

Every run is recorded in ``ingestion_runs`` with its issues and a content hash, so any
backtest can cite exactly which data it used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from apex.db.models import DataQualityIssue, IngestionRun
from apex.domain.market import InstrumentSpec, Timeframe
from apex.marketdata.base import MarketDataProvider
from apex.marketdata.repository import frame_sha256, store_bars, upsert_instrument
from apex.marketdata.validation import ValidationReport, validate_bars

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    run_id: int
    status: str
    received: int
    stored: int
    report: ValidationReport | None
    error: str | None = None


def ingest_bars(session: Session, provider: MarketDataProvider, spec: InstrumentSpec,
                timeframe: Timeframe, start: datetime, end: datetime) -> IngestResult:
    instrument = upsert_instrument(session, spec)
    run = IngestionRun(source=provider.name, instrument_id=instrument.id,
                       timeframe=timeframe.value, range_start=start, range_end=end)
    session.add(run)
    session.flush()
    try:
        raw = provider.fetch_bars(spec, timeframe, start, end)
        report = validate_bars(raw, spec, timeframe, expected_range=(start, end))
        stored = store_bars(session, instrument.id, timeframe, provider.name, report.clean, run.id)
    except Exception as exc:  # record the failure, then re-raise nothing: caller checks status
        log.exception("ingestion failed for %s %s", spec.symbol, timeframe.value)
        run.status, run.error, run.finished_at = "failed", str(exc)[:2000], datetime.now(UTC)
        session.flush()
        return IngestResult(run.id, "failed", 0, 0, None, str(exc))

    for issue in report.issues:
        session.add(DataQualityIssue(run_id=run.id, kind=issue.kind, severity=issue.severity,
                                     ts=issue.ts, detail=issue.detail))
    run.rows_received = len(raw)
    run.rows_stored = stored
    run.content_sha256 = frame_sha256(report.clean)
    run.status = "warn" if report.issues else "ok"
    run.finished_at = datetime.now(UTC)
    session.flush()
    log.info("ingested %s %s from %s: received=%d stored=%d issues=%d", spec.symbol,
             timeframe.value, provider.name, len(raw), stored, len(report.issues))
    return IngestResult(run.id, run.status, len(raw), stored, report)


def update_bars(session: Session, provider: MarketDataProvider, spec: InstrumentSpec,
                timeframe: Timeframe, *, backfill_start: datetime,
                now: datetime | None = None) -> IngestResult | None:
    """Incremental update: fetch from the bar after the newest stored one up to ``now``.

    Only completed bars are requested (the current, still-forming bar is excluded). Returns
    None when already up to date. Safe to run repeatedly, e.g. from cron or a systemd timer.
    """
    from apex.marketdata.repository import latest_bar_ts

    now = now or datetime.now(UTC)
    instrument = upsert_instrument(session, spec)
    last = latest_bar_ts(session, instrument.id, timeframe, provider.name)
    start = backfill_start if last is None else last + timeframe.delta
    step = timeframe.delta
    end = now - (now - datetime(1970, 1, 1, tzinfo=UTC)) % step  # start of forming bar
    if start >= end:
        return None
    return ingest_bars(session, provider, spec, timeframe, start, end)
