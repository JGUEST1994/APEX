"""FastAPI backend (read-only in Phase 1). The dashboard (Phase 10) will consume this."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from apex import __version__
from apex.core.config import Settings, get_settings
from apex.core.errors import UnknownInstrumentError
from apex.db.models import Experiment, IngestionRun, Instrument, ResearchDocument
from apex.db.session import make_engine, make_session_factory
from apex.domain.market import Timeframe
from apex.marketdata.repository import get_instrument, load_bars
from apex.research.library import search


def create_app(settings: Settings | None = None,
               session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    settings = settings or get_settings()
    factory = session_factory or make_session_factory(make_engine(settings.database_url))
    app = FastAPI(title="APEX API", version=__version__)

    def db() -> Iterator[Session]:
        with factory() as s:
            yield s

    @app.get("/health")
    def health(s: Session = Depends(db)) -> dict:
        try:
            s.execute(text("SELECT 1"))
            db_ok = True
        except Exception:  # noqa: BLE001
            db_ok = False
        return {"status": "ok" if db_ok else "degraded", "version": __version__,
                "database": db_ok, "trading_mode": settings.trading_mode.value,
                "live_trading_available": False,
                "external_ai_enabled": settings.ai_provider != "none"}

    @app.get("/instruments")
    def instruments(s: Session = Depends(db)) -> list[dict]:
        rows = s.scalars(select(Instrument).order_by(Instrument.asset_class, Instrument.symbol))
        return [{"symbol": r.symbol, "venue": r.venue, "asset_class": r.asset_class,
                 "calendar": r.calendar, "quote_currency": r.quote_currency} for r in rows]

    @app.get("/instruments/{symbol}/bars")
    def bars(symbol: str, timeframe: Timeframe, source: str,
             start: datetime | None = None, end: datetime | None = None,
             venue: str | None = None, limit: int = Query(5000, le=50000),
             s: Session = Depends(db)) -> list[dict]:
        try:
            inst = get_instrument(s, symbol, venue)
        except UnknownInstrumentError as exc:
            raise HTTPException(404, str(exc)) from exc
        for v in (start, end):
            if v is not None and v.tzinfo is None:
                raise HTTPException(422, "start/end must include a timezone, e.g. Z")
        df = load_bars(s, inst.id, timeframe, source, start, end).tail(limit)
        df["ts"] = df["ts"].map(lambda t: t.isoformat())
        return df.to_dict(orient="records")

    @app.get("/ingestion-runs")
    def runs(limit: int = Query(50, le=500), s: Session = Depends(db)) -> list[dict]:
        rows = s.scalars(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(limit))
        return [{"id": r.id, "source": r.source, "instrument_id": r.instrument_id,
                 "timeframe": r.timeframe, "status": r.status, "rows_received": r.rows_received,
                 "rows_stored": r.rows_stored, "content_sha256": r.content_sha256,
                 "started_at": r.started_at.isoformat()} for r in rows]

    @app.get("/experiments")
    def experiments(limit: int = Query(50, le=500), s: Session = Depends(db)) -> list[dict]:
        rows = s.scalars(select(Experiment).order_by(Experiment.id.desc()).limit(limit))
        return [{"id": e.id, "strategy_version_id": e.strategy_version_id, "kind": e.kind,
                 "simulated": True, "params": e.params, "data_manifest": e.data_manifest,
                 "code_version": e.code_version, "metrics": e.metrics,
                 "created_at": e.created_at.isoformat()} for e in rows]

    @app.get("/research/search")
    def research_search(q: str, limit: int = Query(10, le=100),
                        s: Session = Depends(db)) -> list[dict]:
        return [h.__dict__ for h in search(s, q, limit)]

    @app.get("/research/documents")
    def research_documents(limit: int = Query(100, le=1000),
                           s: Session = Depends(db)) -> list[dict]:
        rows = s.scalars(select(ResearchDocument).order_by(ResearchDocument.id.desc()).limit(limit))
        return [{"id": d.id, "title": d.title, "authors": d.authors, "source_type": d.source_type,
                 "published": d.published, "subjects": d.subjects,
                 "imported_at": d.imported_at.isoformat()} for d in rows]

    @app.get("/research/documents/{doc_id}")
    def research_document(doc_id: int, s: Session = Depends(db)) -> dict:
        d = s.get(ResearchDocument, doc_id)
        if d is None:
            raise HTTPException(404, "document not found")
        return {"id": d.id, "title": d.title, "authors": d.authors, "published": d.published,
                "source_type": d.source_type, "source_ref": d.source_ref,
                "licence_note": d.licence_note, "extracted": d.extracted,
                "chunks": len(d.chunks)}

    return app
