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
    ra = sub.add_parser("research-import",
                        help="import .txt/.md/.html/.pdf/.csv/.vtt/.srt files or a folder")
    ra.add_argument("path")
    ra.add_argument("--title", help="override the extracted title (single file only)")
    ra.add_argument("--licence-note", required=True, help="why APEX may lawfully use it")
    rs = sub.add_parser("research-search")
    rs.add_argument("query")
    bt = sub.add_parser("backtest", help="backtest a registered strategy on stored bars")
    bt.add_argument("--strategy", required=True, help="strategy key, e.g. TF-SMA-001")
    bt.add_argument("--symbol", required=True)
    bt.add_argument("--timeframe", default="1d")
    bt.add_argument("--source", required=True, help="bar source, e.g. synthetic, csv, binance")
    bt.add_argument("--start", required=True, type=_dt)
    bt.add_argument("--end", required=True, type=_dt)
    bt.add_argument("--param", action="append", default=[], help="name=value (repeatable)")
    bt.add_argument("--capital", type=float, default=100_000.0)
    bt.add_argument("--cost-multiplier", type=float, default=1.0)
    sub.add_parser("strategies", help="list registered strategies")
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
        elif args.cmd == "strategies":
            from apex.strategies.registry import all_strategies

            for key, cls in sorted(all_strategies().items()):
                m = cls.meta
                print(f"{key:14} v{m.version:7} {m.category:22} {m.name}")
        elif args.cmd == "backtest":
            import json

            from apex.backtest.engine import BacktestConfig
            from apex.backtest.experiments import run_experiment
            from apex.domain.market import Timeframe
            from apex.strategies.registry import all_strategies

            strategies = all_strategies()
            if args.strategy not in strategies:
                print(f"unknown strategy {args.strategy}; see `apex strategies`", file=sys.stderr)
                return 2
            strat = strategies[args.strategy]()
            params = {}
            for kv in args.param:
                k, _, v = kv.partition("=")
                params[k] = json.loads(v) if v not in ("",) else v
            exp, res = run_experiment(
                s, strat, symbol=args.symbol, timeframe=Timeframe(args.timeframe),
                source=args.source, start=args.start, end=args.end, params=params,
                config=BacktestConfig(initial_capital=args.capital,
                                      cost_multiplier=args.cost_multiplier))
            print(f"experiment {exp.id} (SIMULATED) data_sha256="
                  f"{exp.data_manifest['data_sha256'][:12]} code={exp.code_version[:12]}")
            for k, v in res.metrics.items():
                if k != "note":
                    print(f"  {k:24} {v}")
        elif args.cmd == "research-import":
            from apex.research.extract import SUPPORTED
            from apex.research.library import import_file

            root = Path(args.path)
            files = ([f for f in sorted(root.rglob("*")) if f.suffix.lower() in SUPPORTED]
                     if root.is_dir() else [root])
            for f in files:
                try:
                    doc = import_file(s, f, licence_note=args.licence_note,
                                      title=args.title if len(files) == 1 else None)
                except ValueError as exc:
                    print(f"skipped {f.name}: {exc}", file=sys.stderr)
                    continue
                top = ", ".join(list(doc.extracted.get("concepts", {}))[:5]) or "-"
                print(f"document {doc.id} {doc.title!r}: {len(doc.chunks)} chunks; concepts: {top}")
        elif args.cmd == "research-search":
            from apex.research.library import search

            for h in search(s, args.query):
                print(f"[{h.score:.2f}] {h.title} #{h.chunk_ordinal}: {h.snippet[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
