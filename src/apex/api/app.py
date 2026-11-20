"""FastAPI backend (read-only). Also serves the read-only dashboard at ``/ui/``."""

from __future__ import annotations

import hmac
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from apex import __version__
from apex.core.config import Settings, get_settings
from apex.core.errors import UnknownInstrumentError
from apex.db.models import (
    DataQualityIssue,
    Experiment,
    IngestionRun,
    Instrument,
    PaperAccount,
    PaperFill,
    PaperOrder,
    PaperPosition,
    PriceBar,
    ResearchDocument,
    StrategyVersion,
)
from apex.db.models import Strategy as StrategyRow
from apex.db.session import make_engine, make_session_factory
from apex.domain.market import Timeframe
from apex.marketdata.repository import get_instrument, load_bars
from apex.research.library import search

TOKEN_COOKIE = "apex_token"
OPEN_PATHS = ("/health",)  # container healthchecks need no token


def _require_token(app: FastAPI, settings: Settings) -> None:
    """When ``APEX_API_TOKEN`` is set, every request except /health needs it, as
    ``Authorization: Bearer <token>`` or the cookie set by opening ``/ui/?token=<token>``."""
    if settings.api_token is None or not settings.api_token.get_secret_value():
        return
    token = settings.api_token.get_secret_value().encode()

    def ok(value: str | None) -> bool:
        return value is not None and hmac.compare_digest(value.encode(), token)

    @app.middleware("http")
    async def check_token(request: Request, call_next):  # noqa: ANN001, ANN202
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        given = request.query_params.get("token")
        if request.url.path.startswith("/ui") and ok(given):
            # Swap the token in the address bar for a cookie, so it is not left in history.
            resp = RedirectResponse(request.url.path)
            resp.set_cookie(TOKEN_COOKIE, given, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https")
            return resp
        auth = request.headers.get("authorization", "")
        bearer = auth[7:] if auth.lower().startswith("bearer ") else None
        if ok(bearer) or ok(request.cookies.get(TOKEN_COOKIE)):
            return await call_next(request)
        return PlainTextResponse("APEX needs its access token: open /ui/?token=<your token> "
                                 "once in this browser, or send Authorization: Bearer.",
                                 status_code=401)


def create_app(settings: Settings | None = None,
               session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    settings = settings or get_settings()
    factory = session_factory or make_session_factory(make_engine(settings.database_url))
    app = FastAPI(title="APEX API", version=__version__)
    _require_token(app, settings)

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
        n_issues = (select(DataQualityIssue.run_id, func.count().label("n"))
                    .group_by(DataQualityIssue.run_id).subquery())
        rows = s.execute(select(IngestionRun, Instrument.symbol, n_issues.c.n)
                         .join(Instrument, Instrument.id == IngestionRun.instrument_id)
                         .outerjoin(n_issues, n_issues.c.run_id == IngestionRun.id)
                         .order_by(IngestionRun.id.desc()).limit(limit))
        return [{"id": r.id, "source": r.source, "instrument_id": r.instrument_id,
                 "symbol": sym, "timeframe": r.timeframe, "status": r.status,
                 "rows_received": r.rows_received, "rows_stored": r.rows_stored,
                 "content_sha256": r.content_sha256, "issues": n or 0, "error": r.error,
                 "started_at": r.started_at.isoformat()} for r, sym, n in rows]

    @app.get("/ingestion-runs/{run_id}/issues")
    def run_issues(run_id: int, limit: int = Query(200, le=5000),
                   s: Session = Depends(db)) -> list[dict]:
        """Data-quality findings recorded for one download (gaps, spikes, bad rows...)."""
        if s.get(IngestionRun, run_id) is None:
            raise HTTPException(404, "ingestion run not found")
        rows = s.scalars(select(DataQualityIssue).where(DataQualityIssue.run_id == run_id)
                         .order_by(DataQualityIssue.id).limit(limit))
        return [{"kind": i.kind, "severity": i.severity,
                 "ts": i.ts.isoformat() if i.ts else None, "detail": i.detail} for i in rows]

    @app.get("/experiments")
    def experiments(limit: int = Query(50, le=500), s: Session = Depends(db)) -> list[dict]:
        sv = StrategyVersion
        rows = s.execute(select(Experiment, StrategyRow.key, sv.version)
                         .join(sv, Experiment.strategy_version_id == sv.id)
                         .join(StrategyRow, sv.strategy_id == StrategyRow.id)
                         .order_by(Experiment.id.desc()).limit(limit))
        return [{"id": e.id, "strategy_version_id": e.strategy_version_id, "kind": e.kind,
                 "strategy": key, "strategy_version": ver, "simulated": True,
                 "params": e.params, "data_manifest": e.data_manifest,
                 "code_version": e.code_version, "metrics": e.metrics,
                 "created_at": e.created_at.isoformat()} for e, key, ver in rows]

    @app.get("/leaderboard")
    def get_leaderboard(asset_class: str | None = None, symbol: str | None = None,
                        timeframe: str | None = None, category: str | None = None,
                        s: Session = Depends(db)) -> list[dict]:
        from apex.backtest.tournament import leaderboard

        df = leaderboard(s, asset_class=asset_class, symbol=symbol, timeframe=timeframe,
                         category=category)
        return df.astype(object).where(df.notna(), None).to_dict(orient="records")

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

    @app.get("/strategies")
    def strategies(s: Session = Depends(db)) -> list[dict]:
        from apex.strategies.hypotheses import HYPOTHESES
        from apex.strategies.multi import all_multi_strategies
        from apex.strategies.registry import all_strategies

        ideas: dict[str, list[str]] = {}
        for h in HYPOTHESES:
            if h.implemented:
                ideas.setdefault(h.implemented, []).append(h.id)
        status = {}
        for row, ver in s.execute(select(StrategyRow, StrategyVersion).join(
                StrategyVersion, StrategyVersion.strategy_id == StrategyRow.id)):
            status[row.key] = {"version": ver.version, "status": ver.status}
        return [{"key": k, "name": c.meta.name, "category": c.meta.category,
                 "version": c.meta.version, "regimes": list(c.meta.regimes),
                 "timeframes": [t.value for t in c.meta.timeframes],
                 "description": c.meta.description,
                 "entry_rules": c.meta.entry_rules, "exit_rules": c.meta.exit_rules,
                 "stop_loss": c.meta.stop_loss, "take_profit": c.meta.take_profit,
                 "asset_classes": [a.value for a in c.meta.asset_classes],
                 "default_params": c.meta.default_params, "ideas": ideas.get(k, []),
                 "status": status.get(k, {}).get("status", "experimental"),
                 "instruments": getattr(c, "legs", 1) or "2+"}
                for k, c in sorted({**all_strategies(), **all_multi_strategies()}.items())]

    @app.get("/system/checks")
    def system_checks() -> list[dict]:
        """The `apex doctor` check-up (read-only; says whether keys are set, never their
        values)."""
        from dataclasses import asdict

        from apex.doctor import run_checks

        return [asdict(c) for c in run_checks(settings)]

    @app.get("/hypotheses")
    def hypotheses() -> list[dict]:
        """The strategy hypothesis bank (spec section 8) and which strategy codes each."""
        from dataclasses import asdict

        from apex.strategies.hypotheses import HYPOTHESES

        return [asdict(h) for h in HYPOTHESES]

    @app.get("/datasets")
    def datasets(s: Session = Depends(db)) -> list[dict]:
        q = (select(Instrument.symbol, PriceBar.timeframe, PriceBar.source,
                    func.count(), func.min(PriceBar.ts), func.max(PriceBar.ts))
             .join(Instrument, Instrument.id == PriceBar.instrument_id)
             .group_by(Instrument.symbol, PriceBar.timeframe, PriceBar.source)
             .order_by(Instrument.symbol, PriceBar.timeframe, PriceBar.source))
        return [{"symbol": sym, "timeframe": tf, "source": src, "bars": n,
                 "first": lo.isoformat() if lo else None, "last": hi.isoformat() if hi else None}
                for sym, tf, src, n, lo, hi in s.execute(q)]

    def _account(a: PaperAccount, s: Session) -> dict:
        units = {p.symbol: p.units for p in s.scalars(
            select(PaperPosition).where(PaperPosition.account_id == a.id))}
        return {"name": a.name, "strategy": a.strategy_key, "symbol": a.symbol,
                "symbols": a.all_symbols,
                "timeframe": a.timeframe, "source": a.source, "params": a.params,
                "status": a.status, "initial_capital": a.initial_capital, "cash": a.cash,
                "realised": a.realised, "peak_equity": a.peak_equity,
                "consecutive_losses": a.consecutive_losses, "units": units,
                "limits": a.limits,
                "last_bar_ts": a.last_bar_ts.isoformat() if a.last_bar_ts else None,
                "kill_switch": {"engaged": a.kill_engaged, "reason": a.kill_reason,
                                "at": a.kill_at.isoformat() if a.kill_at else None,
                                "by": a.kill_by},
                "simulated": True}

    # Paper (simulated) accounts are read-only here. Creating, running and resetting the
    # kill switch stay on the command line, where a named human is recorded.
    @app.get("/paper/accounts")
    def paper_accounts(s: Session = Depends(db)) -> list[dict]:
        return [_account(a, s) for a in s.scalars(select(PaperAccount).order_by(PaperAccount.name))]

    @app.get("/paper/accounts/{name}")
    def paper_account(name: str, limit: int = Query(200, le=5000),
                      s: Session = Depends(db)) -> dict:
        a = s.scalar(select(PaperAccount).where(PaperAccount.name == name))
        if a is None:
            raise HTTPException(404, "paper account not found")
        fills = s.scalars(select(PaperFill).where(PaperFill.account_id == a.id)
                          .order_by(PaperFill.id.desc()).limit(limit))
        orders = s.scalars(select(PaperOrder).where(PaperOrder.account_id == a.id)
                           .order_by(PaperOrder.id.desc()).limit(limit))
        out = _account(a, s)
        out["fills"] = [{"client_order_id": f.client_order_id, "symbol": f.symbol, "qty": f.qty,
                         "price": f.price, "cost": f.cost, "ts": f.ts.isoformat()} for f in fills]
        out["orders"] = [{"client_order_id": o.client_order_id, "symbol": o.symbol, "qty": o.qty,
                          "status": o.status, "filled_qty": o.filled_qty,
                          "avg_price": o.avg_price, "reason": o.reason,
                          "signal_ts": o.signal_ts.isoformat()} for o in orders]
        return out

    @app.get("/paper/accounts/{name}/consistency")
    def paper_consistency(name: str, s: Session = Depends(db)) -> dict:
        from apex.execution.consistency import compare

        try:
            return compare(s, name)
        except KeyError as exc:
            raise HTTPException(404, "paper account not found") from exc

    static = Path(__file__).parent / "static"
    app.mount("/ui", StaticFiles(directory=static, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    return app
