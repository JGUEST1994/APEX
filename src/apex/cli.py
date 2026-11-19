"""Command-line entry point: ``apex <command>``. Runs without any external AI service."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from apex.core.config import get_settings
from apex.core.logging import configure_logging


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="apex")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("db-upgrade", help="apply database migrations")
    sub.add_parser("seed-instruments", help="load the default instrument universe")
    ing = sub.add_parser("ingest", help="ingest historical bars")
    ing.add_argument("--provider", choices=["synthetic", "csv", "binance"], required=True)
    ing.add_argument("--symbol", required=True)
    ing.add_argument("--timeframe", default="1d")
    ing.add_argument("--start", required=True, type=_dt)
    ing.add_argument("--end", required=True, type=_dt)
    ing.add_argument("--csv-root", default="data/csv")
    ra = sub.add_parser("research-add", help="add a plain-text document to the library")
    ra.add_argument("path")
    ra.add_argument("--title", required=True)
    ra.add_argument("--licence-note", default="", help="why we may lawfully use it")
    rs = sub.add_parser("research-search")
    rs.add_argument("query")
    sv = sub.add_parser("serve", help="run the API server")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    if args.cmd == "db-upgrade":
        from apex.db.migrate import upgrade

        upgrade(settings.database_url)
        print("database at head")
        return 0
    if args.cmd == "serve":
        import uvicorn

        from apex.api.app import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return 0

    from apex.db.session import make_engine, make_session_factory, session_scope

    factory = make_session_factory(make_engine(settings.database_url))
    with session_scope(factory) as s:
        if args.cmd == "seed-instruments":
            from apex.domain.instruments import DEFAULT_UNIVERSE
            from apex.marketdata.repository import upsert_instrument

            for spec in DEFAULT_UNIVERSE:
                upsert_instrument(s, spec)
            print(f"{len(DEFAULT_UNIVERSE)} instruments seeded")
        elif args.cmd == "ingest":
            from apex.domain.instruments import by_symbol
            from apex.domain.market import Timeframe
            from apex.marketdata.ingest import ingest_bars

            spec = by_symbol().get(args.symbol)
            if spec is None:
                print(f"unknown symbol {args.symbol}", file=sys.stderr)
                return 2
            if args.provider == "synthetic":
                from apex.marketdata.providers.synthetic import SyntheticProvider as P

                provider = P()
            elif args.provider == "csv":
                from apex.marketdata.providers.csv_file import CsvFileProvider

                provider = CsvFileProvider(Path(args.csv_root))
            else:
                from apex.marketdata.providers.binance import BinanceSpotProvider

                provider = BinanceSpotProvider()
            r = ingest_bars(s, provider, spec, Timeframe(args.timeframe), args.start, args.end)
            print(f"run {r.run_id}: {r.status} received={r.received} stored={r.stored}"
                  + (f" issues={len(r.report.issues)}" if r.report else f" error={r.error}"))
            return 0 if r.status != "failed" else 1
        elif args.cmd == "research-add":
            from apex.research.library import add_document

            doc = add_document(s, title=args.title, text=Path(args.path).read_text(),
                               source_type="doc", source_ref=args.path,
                               licence_note=args.licence_note)
            print(f"document {doc.id}: {len(doc.chunks)} chunks")
        elif args.cmd == "research-search":
            from apex.research.library import keyword_search

            for h in keyword_search(s, args.query):
                print(f"[{h.score:.2f}] {h.title} #{h.chunk_ordinal}: {h.snippet[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
