"""Command-line entry point: ``apex <command>``. Runs without any external AI service."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from apex.core.config import get_settings
from apex.core.logging import configure_logging


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


PROVIDERS = ["synthetic", "csv", "csv-cont", "csv-cont-ratio", "dukascopy-csv", "binance",
             "kraken", "coinbase", "alpaca", "polygon", "tiingo"]


def _param(v: str):  # noqa: ANN202
    """A --param value: JSON, or @path to read the JSON from a file."""
    if v.startswith("@"):
        return json.loads(Path(v[1:]).read_text())
    return json.loads(v)


def make_provider(name: str, settings, csv_root: str = "data/csv"):  # noqa: ANN001, ANN201
    if name == "synthetic":
        from apex.marketdata.providers.synthetic import SyntheticProvider

        return SyntheticProvider()
    if name == "csv":
        from apex.marketdata.providers.csv_file import CsvFileProvider

        return CsvFileProvider(Path(csv_root))
    if name == "dukascopy-csv":
        from apex.marketdata.providers.dukascopy_csv import DukascopyCsvProvider

        return DukascopyCsvProvider(Path(csv_root))
    if name in ("csv-cont", "csv-cont-ratio"):
        from apex.marketdata.providers.continuous import ContinuousFuturesProvider

        return ContinuousFuturesProvider(Path(csv_root),
                                         "ratio" if name.endswith("ratio") else "difference")
    if name == "binance":
        from apex.marketdata.providers.binance import BinanceSpotProvider

        return BinanceSpotProvider()
    if name == "alpaca":
        from apex.marketdata.providers.alpaca import AlpacaStocksProvider

        if not (settings.alpaca_key_id and settings.alpaca_secret_key):
            raise SystemExit("set APEX_ALPACA_KEY_ID and APEX_ALPACA_SECRET_KEY")
        return AlpacaStocksProvider(settings.alpaca_key_id.get_secret_value(),
                                    settings.alpaca_secret_key.get_secret_value(),
                                    feed=settings.alpaca_feed)
    if name == "kraken":
        from apex.marketdata.providers.kraken import KrakenSpotProvider

        return KrakenSpotProvider()
    if name == "coinbase":
        from apex.marketdata.providers.coinbase import CoinbaseSpotProvider

        return CoinbaseSpotProvider()
    if name == "tiingo":
        from apex.marketdata.providers.tiingo import TiingoProvider

        if not settings.tiingo_api_key:
            raise SystemExit("set APEX_TIINGO_API_KEY (free at tiingo.com)")
        return TiingoProvider(settings.tiingo_api_key.get_secret_value())
    if name == "polygon":
        from apex.marketdata.providers.polygon import PolygonStocksProvider

        if not settings.polygon_api_key:
            raise SystemExit("set APEX_POLYGON_API_KEY")
        return PolygonStocksProvider(settings.polygon_api_key.get_secret_value())
    raise SystemExit(f"unknown provider {name}")


def _export(s, args) -> int:  # noqa: ANN001
    from pathlib import Path

    from apex import export

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"{out} already exists; use --force to overwrite it", file=sys.stderr)
        return 2
    if args.what == "bars":
        if not (args.symbol and args.timeframe and args.source):
            print("bars needs --symbol, --timeframe and --source", file=sys.stderr)
            return 2
        from apex.core.errors import UnknownInstrumentError

        try:
            df = export.bars(s, args.symbol, args.timeframe, args.source)
        except (UnknownInstrumentError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    elif args.what == "fills":
        if not args.name:
            print("fills needs --name (the practice account)", file=sys.stderr)
            return 2
        try:
            df = export.fills(s, args.name)
        except LookupError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    else:
        df = getattr(export, args.what)(s)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"{len(df)} row(s) written to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="apex")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("db-upgrade", help="apply database migrations")
    sub.add_parser("seed-instruments", help="load the default instrument universe")
    ing = sub.add_parser("ingest", help="ingest historical bars")
    ing.add_argument("--provider", choices=PROVIDERS, required=True)
    ing.add_argument("--symbol", required=True)
    ing.add_argument("--timeframe", default="1d")
    ing.add_argument("--start", required=True, type=_dt)
    ing.add_argument("--end", required=True, type=_dt)
    ing.add_argument("--csv-root", default="data/csv")
    ing.add_argument("--reuse", action="store_true",
                     help="skip the download when exactly this request is already stored")
    pc = sub.add_parser("price-cache-save",
                        help="copy the SQLite database without research results, for reuse")
    pc.add_argument("--out", required=True)
    up = sub.add_parser("update", help="incremental update of completed bars (cron-safe)")
    up.add_argument("--provider", choices=PROVIDERS, required=True)
    up.add_argument("--symbols", required=True, help="comma-separated")
    up.add_argument("--timeframe", default="1m")
    up.add_argument("--backfill-start", required=True, type=_dt,
                    help="start date when nothing is stored yet")
    up.add_argument("--csv-root", default="data/csv")
    rsp = sub.add_parser("resample", help="derive a coarser timeframe from stored bars")
    rsp.add_argument("--symbol", required=True)
    rsp.add_argument("--source", required=True)
    rsp.add_argument("--from-timeframe", required=True)
    rsp.add_argument("--to-timeframe", required=True)
    rsp.add_argument("--start", required=True, type=_dt)
    rsp.add_argument("--end", required=True, type=_dt)
    rc = sub.add_parser("reconcile", help="compare one instrument across two sources")
    rc.add_argument("--symbol", required=True)
    rc.add_argument("--timeframe", default="1d")
    rc.add_argument("--sources", required=True, help="two sources, comma-separated")
    rc.add_argument("--start", type=_dt)
    rc.add_argument("--end", type=_dt)
    rc.add_argument("--tolerance-bps", type=float, default=5.0)
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
    bt.add_argument("--regime-filter", action="store_true",
                    help="trade only in the regimes the strategy declares")
    va = sub.add_parser("validate", help="walk-forward + PBO + Monte Carlo validation")
    va.add_argument("--strategy", required=True)
    va.add_argument("--symbol", required=True,
                    help="instrument, or legs as A/B for a multi-instrument strategy")
    va.add_argument("--timeframe", default="1d")
    va.add_argument("--source", required=True)
    va.add_argument("--start", required=True, type=_dt)
    va.add_argument("--end", required=True, type=_dt)
    va.add_argument("--grid", action="append", default=[],
                    help="name=v1,v2,... (repeatable); values parsed as JSON")
    va.add_argument("--train-bars", type=int, required=True)
    va.add_argument("--test-bars", type=int, required=True)
    va.add_argument("--anchored", action="store_true")
    va.add_argument("--regime-filter", action="store_true")
    tn = sub.add_parser("tournament", help="validate many strategies and rank them")
    tn.add_argument("--only-suited", action="store_true",
                    help="skip strategies on markets or timeframes they are not meant for "
                    "(always on with --strategies all)")
    tn.add_argument("--strategies", default="all", help="comma-separated keys or 'all'")
    tn.add_argument("--exclude", default="", help="comma-separated keys to leave out")
    tn.add_argument("--param", action="append", default=[],
                    help="key=value (JSON, or @file) for each chosen strategy that has that "
                    "setting, e.g. anchors=@data/events/AAPL_earnings.json")
    tn.add_argument("--symbols", required=True,
                    help="comma-separated; pairs as A/B go to multi-instrument strategies")
    tn.add_argument("--timeframe", default="1d")
    tn.add_argument("--source", required=True)
    tn.add_argument("--start", required=True, type=_dt)
    tn.add_argument("--end", required=True, type=_dt)
    tn.add_argument("--train-bars", type=int, required=True)
    tn.add_argument("--test-bars", type=int, required=True)
    tn.add_argument("--no-promote", action="store_true")
    tn.add_argument("--regime-filter", action="store_true",
                    help="also enter regime-filtered variants (<KEY>+RF)")
    lb = sub.add_parser("leaderboard", help="show the strategy leaderboard")
    for opt in ("--asset-class", "--symbol", "--timeframe", "--category"):
        lb.add_argument(opt)
    sl = sub.add_parser("shortlist", help="plain-English shortlist of strategies worth a "
                        "30-day practice run, and why the rest failed (read-only)")
    sl.add_argument("--top", type=int, default=5)
    rg = sub.add_parser("regimes", help="regime labels, stability and OOS check for stored bars")
    rg.add_argument("--symbol", required=True)
    rg.add_argument("--timeframe", default="1d")
    rg.add_argument("--source", required=True)
    rg.add_argument("--start", required=True, type=_dt)
    rg.add_argument("--end", required=True, type=_dt)
    rg.add_argument("--train-frac", type=float, default=0.5)
    pc = sub.add_parser("paper-create", help="create a simulated paper account")
    pc.add_argument("--name", required=True)
    pc.add_argument("--strategy", required=True)
    pc.add_argument("--symbol", required=True,
                    help="one symbol, or several separated by commas (shared cash and risk)")
    pc.add_argument("--timeframe", default="1d")
    pc.add_argument("--source", required=True)
    pc.add_argument("--capital", type=float, default=100_000.0)
    pc.add_argument("--param", action="append", default=[],
                    help="strategy setting name=value (JSON value, repeatable)")
    pc.add_argument("--start-now", action="store_true",
                    help="trade only prices that arrive from now on, instead of first "
                         "replaying every stored bar (use this for a 30-day practice run)")
    pc.add_argument("--limit", action="append", default=[], metavar="NAME=VALUE",
                    help="a safety limit for this account only, e.g. max_positions=13 "
                         "(repeatable); a looser limit than the standard needs --by")
    pc.add_argument("--by", default="",
                    help="your name, recorded in the audit log (needed to loosen a limit)")
    pr = sub.add_parser("paper-run", help="process new stored bars for a paper account "
                        "(restart-safe; run from cron or a scheduler)")
    pr.add_argument("--name", required=True)
    pr.add_argument("--end", type=_dt)
    pk = sub.add_parser("paper-reset-kill", help="human reset of a paper account kill switch")
    pk.add_argument("--name", required=True)
    pk.add_argument("--actor", required=True, help="your name (recorded in the audit log)")
    ed = sub.add_parser("earnings-dates", help="download a US company's earnings-release "
                        "dates from SEC EDGAR (free; needs APEX_SEC_USER_AGENT) for "
                        "VWAP-EARN-001")
    ed.add_argument("--symbol", required=True)
    ed.add_argument("--out", default="data/events")
    nf = sub.add_parser("news-fetch", help="read the free news and official-release feeds "
                        "once and store what's new (see apex news-feeds)")
    nf.add_argument("--feeds", nargs="*", help="feed names (default: all)")
    nl = sub.add_parser("news", help="show the latest stored news, newest first")
    nl.add_argument("--symbol")
    nl.add_argument("--limit", type=int, default=20)
    sub.add_parser("news-feeds", help="list the news feeds APEX can read")
    mf = sub.add_parser("macro-fetch", help="download the economic calendar and macro series "
                        "(fear index, rates, stress) from FRED/ALFRED (free key)")
    mf.add_argument("--series", nargs="*", help="series ids (default: all; see apex macro)")
    mf.add_argument("--no-calendar", action="store_true")
    mc = sub.add_parser("macro", help="show coming economic releases and the latest macro values")
    mc.add_argument("--days", type=int, default=14)
    pq = sub.add_parser("paper-compare", help="compare a paper account with a backtest of "
                        "the same strategy over the same bars (read-only)")
    pq.add_argument("--name", required=True)
    sa = sub.add_parser("strategy-approve", help="a named person approves the strategy a "
                        "practice account runs, only if its practice record is READY")
    sa.add_argument("--account", required=True, help="practice account with a READY record")
    sa.add_argument("--by", required=True, help="your name (recorded in the audit log)")
    sa.add_argument("--reason", default="")
    pr = sub.add_parser("paper-record", help="is this practice account's record long and "
                        "clean enough to consider approving its strategy? (read-only)")
    pr.add_argument("--name", required=True)
    bp = sub.add_parser("backtest-pair", help="backtest a strategy that trades several "
                        "instruments together, e.g. STAT-PAIR-001 (SIMULATED)")
    bp.add_argument("--strategy", default="STAT-PAIR-001")
    bp.add_argument("--symbols", required=True, help="comma-separated, in leg order")
    bp.add_argument("--timeframe", default="1d")
    bp.add_argument("--source", required=True)
    bp.add_argument("--start", required=True, type=_dt)
    bp.add_argument("--end", required=True, type=_dt)
    bp.add_argument("--param", action="append", default=[], help="key=value (JSON value)")
    bp.add_argument("--cost-multiplier", type=float, default=1.0)
    sc = sub.add_parser("schedule", help="run data updates and paper accounts on a timer "
                        "(see docs/DEPLOYMENT.md)")
    sc.add_argument("--config", required=True, help="JSON job file")
    sc.add_argument("--once", action="store_true", help="run every job once and exit")
    sub.add_parser("strategies", help="list registered strategies")
    sub.add_parser("hypotheses", help="print the strategy hypothesis bank as Markdown")
    ex = sub.add_parser("export", help="save results as a CSV file (opens in Excel)")
    ex.add_argument("what", choices=["experiments", "leaderboard", "bars", "fills"])
    ex.add_argument("--out", required=True, help="CSV file to write")
    ex.add_argument("--force", action="store_true", help="overwrite the file if it exists")
    ex.add_argument("--symbol")
    ex.add_argument("--timeframe")
    ex.add_argument("--source")
    ex.add_argument("--name", help="practice account (for fills)")
    sub.add_parser("doctor", help="check this install: database, tables, keys, data, "
                   "practice accounts (read-only)")
    bk = sub.add_parser("backup", help="copy the SQLite database to a dated file in a "
                        "backups folder (safe while APEX is running)")
    bk.add_argument("--folder", default="backups", help="where to put the copy")
    bk.add_argument("--keep", type=int, help="keep only the newest N backups there")
    qs = sub.add_parser("quickstart", help="first run: set up, download free daily prices "
                        "and run one example backtest")
    qs.add_argument("--provider", choices=PROVIDERS, default="coinbase")
    qs.add_argument("--symbol", default="BTCUSDT")
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
    if args.cmd == "doctor":
        from apex.doctor import FAIL, render, run_checks

        checks = run_checks(settings)
        print(render(checks))
        return 1 if any(c.level == FAIL for c in checks) else 0
    if args.cmd == "quickstart":
        from apex.quickstart import run as quickstart

        return quickstart(settings, args.provider, args.symbol)
    if args.cmd == "backup":
        from apex.backup import BackupError
        from apex.backup import backup as do_backup

        try:
            dest = do_backup(settings.database_url, Path(args.folder), keep=args.keep)
        except BackupError as exc:
            print(f"backup not made: {exc}", file=sys.stderr)
            return 1
        print(f"backup saved: {dest} (checked OK)")
        return 0
    if args.cmd == "earnings-dates":
        from apex.core.errors import ProviderError
        from apex.marketdata.earnings import SecEarningsDates, anchor_dates

        try:
            rel = SecEarningsDates(settings.sec_user_agent).releases(args.symbol)
        except ProviderError as exc:
            print(exc, file=sys.stderr)
            return 1
        days = anchor_dates(rel)
        out = Path(args.out) / f"{args.symbol.upper()}_earnings.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(days, indent=0) + "\n")
        print(f"{len(days)} earnings releases for {args.symbol.upper()} "
              f"({days[0] if days else '-'} to {days[-1] if days else '-'}) saved to {out}")
        print(f"use them with: --strategy VWAP-EARN-001 --param anchors=@{out.as_posix()}")
        return 0
    if args.cmd == "news-feeds":
        from apex.news.sources import FEEDS

        for f in FEEDS.values():
            print(f"{f.name:13} every {f.every_minutes:>3} min  {f.label}")
        return 0
    if args.cmd == "hypotheses":
        from apex.strategies.hypotheses import to_markdown

        print(to_markdown())
        return 0
    if args.cmd == "serve":
        import uvicorn

        from apex.api.app import create_app

        # log_config=None: uvicorn's own loggers propagate to our redacting handler, so a
        # token in a request line (e.g. /ui/?token=...) is never written to the log.
        uvicorn.run(create_app(settings), host=args.host, port=args.port, log_config=None)
        return 0

    from apex.db.session import make_engine, make_session_factory, session_scope

    factory = make_session_factory(make_engine(settings.database_url))
    with session_scope(factory) as s:
        if args.cmd == "export":
            return _export(s, args)
        if args.cmd == "news-fetch":
            from apex.news.fetch import fetch_feeds, fetcher_for

            r = fetch_feeds(s, fetcher_for(settings), args.feeds or None)
            for name, c in r["stored"].items():
                print(f"{name}: {c['new']} new, {c['changed']} changed, {c['unchanged']} unchanged")
            for e in r["errors"]:
                print(f"skipped {e}", file=sys.stderr)
            return 1 if r["errors"] and not r["stored"] else 0
        if args.cmd == "macro-fetch":
            from apex.core.errors import ProviderError
            from apex.news.fred import FredClient
            from apex.news.macro import fetch_macro

            key = settings.fred_api_key
            try:
                fred = FredClient(key.get_secret_value() if key else "")
            except ProviderError as exc:
                print(exc, file=sys.stderr)
                return 1
            r = fetch_macro(s, fred, series=args.series or None, releases=not args.no_calendar)
            for name, n in r["events"].items():
                print(f"calendar  {name}: {n} new dates")
            for sid, n in r["series"].items():
                print(f"series    {sid}: {n} new values")
            for e in r["errors"]:
                print(f"skipped {e}", file=sys.stderr)
            return 1 if r["errors"] and not (r["events"] or r["series"]) else 0
        if args.cmd == "macro":
            from datetime import UTC, datetime

            from apex.news.macro import latest_values, upcoming

            for ev in upcoming(s, datetime.now(UTC).date(), args.days):
                print(f"{ev.event_date}  {ev.category:9} {ev.name}")
            for v in latest_values(s):
                print(f"{v['date']}  {v['label']}: {v['value']:g} {v['unit']}")
            return 0
        if args.cmd == "news":
            from apex.news.store import label, latest

            for it in latest(s, limit=args.limit, symbol=args.symbol):
                syms = ",".join(x.symbol for x in it.symbols)
                print(f"{it.published_at:%Y-%m-%d %H:%M} UTC  {it.source:12} {label(it):10} "
                      f"{syms + '  ' if syms else ''}{it.title}")
            return 0
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
            provider = make_provider(args.provider, settings, args.csv_root)
            if args.reuse:
                from apex.price_cache import stored_run

                old = stored_run(s, provider.name, spec.symbol, Timeframe(args.timeframe).value,
                                 args.start, args.end)
                if old is not None:
                    print(f"run {old.id}: reused stored={old.rows_stored} (already downloaded)")
                    return 0
            r = ingest_bars(s, provider, spec, Timeframe(args.timeframe), args.start, args.end)
            print(f"run {r.run_id}: {r.status} received={r.received} stored={r.stored}"
                  + (f" issues={len(r.report.issues)}" if r.report else f" error={r.error}"))
            for roll in getattr(provider, "last_rolls", []):
                print(f"  rolled {roll.old} -> {roll.new} after {roll.ts:%Y-%m-%d %H:%M} UTC "
                      f"({roll.reason}, gap {roll.gap:+g})")
            return 0 if r.status != "failed" else 1
        elif args.cmd == "price-cache-save":
            from apex.price_cache import save_prices_only

            s.commit()
            try:
                bars = save_prices_only(settings.database_url, Path(args.out))
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            print(f"saved {bars} bars to {args.out} (research results left out)")
        elif args.cmd == "update":
            from apex.domain.instruments import by_symbol
            from apex.domain.market import Timeframe
            from apex.marketdata.ingest import update_bars

            provider = make_provider(args.provider, settings, args.csv_root)
            universe, rc_ = by_symbol(), 0
            for sym in [x.strip() for x in args.symbols.split(",") if x.strip()]:
                if sym not in universe:
                    print(f"{sym}: unknown symbol", file=sys.stderr)
                    rc_ = 2
                    continue
                r = update_bars(s, provider, universe[sym], Timeframe(args.timeframe),
                                backfill_start=args.backfill_start)
                if r is None:
                    print(f"{sym}: up to date")
                else:
                    print(f"{sym}: run {r.run_id} {r.status} stored={r.stored}")
                    rc_ = rc_ or (1 if r.status == "failed" else 0)
                s.commit()  # keep completed symbols even if a later one fails
            return rc_
        elif args.cmd == "resample":
            from apex.domain.market import Timeframe
            from apex.marketdata.ingest import ingest_bars
            from apex.marketdata.providers.stored import ResampledProvider
            from apex.marketdata.repository import get_instrument, to_spec

            spec = to_spec(get_instrument(s, args.symbol))
            prov = ResampledProvider(s, args.source, Timeframe(args.from_timeframe))
            r = ingest_bars(s, prov, spec, Timeframe(args.to_timeframe), args.start, args.end)
            print(f"run {r.run_id}: {r.status} source={prov.name} stored={r.stored}")
            return 0 if r.status != "failed" else 1
        elif args.cmd == "reconcile":
            from apex.domain.market import Timeframe
            from apex.marketdata.reconcile import reconcile
            from apex.marketdata.repository import get_instrument, load_bars

            a_src, b_src = [x.strip() for x in args.sources.split(",")]
            inst = get_instrument(s, args.symbol)
            tf = Timeframe(args.timeframe)
            rep = reconcile(load_bars(s, inst.id, tf, a_src, args.start, args.end),
                            load_bars(s, inst.id, tf, b_src, args.start, args.end),
                            args.tolerance_bps)
            print(f"compared={rep.compared} only_{a_src}={len(rep.only_in_a)} "
                  f"only_{b_src}={len(rep.only_in_b)} mismatches={len(rep.mismatches)}")
            if not rep.mismatches.empty:
                print(rep.mismatches.head(20).to_string(index=False))
            return 0 if rep.ok else 1
        elif args.cmd == "validate":

            from apex.backtest.experiments import run_validation
            from apex.domain.market import Timeframe
            from apex.strategies.multi import all_multi_strategies
            from apex.strategies.registry import all_strategies

            strategies = {**all_strategies(), **all_multi_strategies()}
            if args.strategy not in strategies:
                print(f"unknown strategy {args.strategy}", file=sys.stderr)
                return 2
            grid = {}
            for g in args.grid:
                k, _, vs = g.partition("=")
                grid[k] = [json.loads(v) for v in vs.split(",")]
            strat = strategies[args.strategy]()
            if args.regime_filter:
                from apex.regime.filtered import RegimeFiltered
                from apex.strategies.multi import MultiStrategy

                if isinstance(strat, MultiStrategy):
                    print("--regime-filter is for single-instrument strategies",
                          file=sys.stderr)
                    return 2
                strat = RegimeFiltered(strat)
            exp = run_validation(s, strat, symbol=args.symbol,
                                 timeframe=Timeframe(args.timeframe), source=args.source,
                                 start=args.start, end=args.end, grid=grid,
                                 train_bars=args.train_bars, test_bars=args.test_bars,
                                 anchored=args.anchored)
            print(f"experiment {exp.id} walk-forward (SIMULATED, out-of-sample only)")
            for k, v in exp.metrics.items():
                if k not in ("folds_detail",):
                    print(f"  {k:26} {v}")
        elif args.cmd == "tournament":
            from apex.backtest.tournament import run_tournament
            from apex.domain.market import Timeframe
            from apex.strategies.multi import all_multi_strategies
            from apex.strategies.registry import all_strategies

            reg = {**all_strategies(), **all_multi_strategies()}
            keys = sorted(reg) if args.strategies == "all" else args.strategies.split(",")
            skip = [k for k in args.exclude.split(",") if k]
            missing = [k for k in keys + skip if k not in reg]
            if missing:
                print(f"unknown strategies: {missing}", file=sys.stderr)
                return 2
            strats = [reg[k]() for k in keys if k not in skip]
            given = {}
            for kv in args.param:
                k, _, v = kv.partition("=")
                given[k] = _param(v)
            # a given setting replaces the default of each strategy that has it (still one
            # set of settings per strategy, so nothing is tuned)
            grids = {st.meta.key: {k: [given.get(k, v)] for k, v in
                                   st.meta.default_params.items()}
                     for st in strats if set(given) & set(st.meta.default_params)}
            unused = [k for k in given if not any(k in st.meta.default_params for st in strats)]
            if unused:
                print(f"--param {', '.join(unused)}: none of the chosen strategies has that "
                      "setting", file=sys.stderr)
                return 2
            entries = run_tournament(
                s, strats, symbols=args.symbols.split(","), grids=grids or None,
                timeframe=Timeframe(args.timeframe), source=args.source, start=args.start,
                end=args.end, train_bars=args.train_bars, test_bars=args.test_bars,
                promote=not args.no_promote, regime_filter=args.regime_filter,
                suited_only=args.only_suited or args.strategies == "all")
            print("TOURNAMENT (SIMULATED, out-of-sample, trial-adjusted)")
            for e in entries:
                verdict = "PASS" if e.passed else "fail"
                print(f"{verdict:4} {e.score:6.3f} {e.strategy_key:14} {e.symbol:8} "
                      f"{e.timeframe:4} {'; '.join(e.reasons)[:110]}")
        elif args.cmd == "backtest-pair":

            from apex.backtest.engine import BacktestConfig
            from apex.backtest.experiments import run_pair_experiment
            from apex.domain.market import Timeframe
            from apex.strategies.multi import all_multi_strategies

            multi = all_multi_strategies()
            if args.strategy not in multi:
                print(f"unknown multi-instrument strategy {args.strategy}; "
                      f"available: {', '.join(sorted(multi))}", file=sys.stderr)
                return 2
            params = {}
            for kv in args.param:
                k, _, v = kv.partition("=")
                params[k] = _param(v)
            exp, res = run_pair_experiment(
                s, multi[args.strategy](), symbols=[x.strip() for x in args.symbols.split(",")],
                timeframe=Timeframe(args.timeframe), source=args.source, start=args.start,
                end=args.end, params=params,
                config=BacktestConfig(cost_multiplier=args.cost_multiplier))
            s.commit()
            print(f"experiment {exp.id} (SIMULATED) {exp.data_manifest['symbol']}")
            for k, v in res.metrics.items():
                if k != "note":
                    print(f"  {k:24} {v}")
        elif args.cmd == "schedule":

            from apex.db.session import make_engine, make_session_factory
            from apex.scheduler import Scheduler

            sched = Scheduler.from_file(args.config, settings,
                                        make_session_factory(make_engine(settings.database_url)))
            if args.once:
                res = sched.run_cycle()
                for r in res:
                    print(json.dumps(r, default=str))
                return 0 if all(r["status"] == "ok" for r in res) else 1
            print(f"scheduler started with {len(sched.jobs)} jobs (Ctrl+C to stop)")
            try:
                sched.run_forever()
            except KeyboardInterrupt:
                print("scheduler stopped")
            return 0
        elif args.cmd == "paper-compare":
            from apex.execution.consistency import compare

            rep = compare(s, args.name)
            for k, v in rep.items():
                print(f"{k:24} {v}")
            return 0 if rep.get("consistent") else 4
        elif args.cmd == "strategy-approve":
            from apex.approval import ApprovalRefused, approve

            try:
                ver = approve(s, args.account, by=args.by, reason=args.reason)
            except (ApprovalRefused, PermissionError, KeyError) as exc:
                print(f"not approved: {exc}", file=sys.stderr)
                return 4
            s.commit()
            print(f"approved by {args.by}: strategy version {ver.version} "
                  f"(id {ver.id}); recorded in the audit log. This enables no real-money "
                  "trading.")
            return 0
        elif args.cmd == "paper-record":
            from apex.paper_record import record

            rep = record(s, args.name)
            print(f"{rep['account']}: {rep['strategy']} on {', '.join(rep['symbols'])} "
                  "(SIMULATED)")
            print(f"  {rep['days']} of {rep['min_days']} days since {rep['started'][:10]}, "
                  f"still running: {'yes' if rep['running'] else 'no'}")
            if rep["limits_changed"]:
                print("  safety limits differ from the standard: " + ", ".join(
                    f"{k} {v['account']} (standard {v['standard']})"
                    for k, v in rep["limits_changed"].items()))
            if rep["ready"]:
                print("  READY: the record is long and clean enough for a person to "
                      "consider approving the strategy")
                return 0
            print("  NOT READY:")
            for r in rep["reasons"]:
                print(f"  - {r}")
            return 4
        elif args.cmd in ("paper-create", "paper-run", "paper-reset-kill"):
            from datetime import UTC, datetime

            from apex.core.config import TradingMode
            from apex.core.errors import ApexError
            from apex.execution import store

            if settings.trading_mode is not TradingMode.PAPER:
                print("paper commands need APEX_TRADING_MODE=paper", file=sys.stderr)
                return 2
            now = datetime.now(UTC)
            if args.cmd == "paper-create":
                from apex.risk.limits import RiskLimits

                given, limit_changes = {}, {}
                try:
                    for kv in args.param:
                        k, _, v = kv.partition("=")
                        given[k] = _param(v)
                    for kv in args.limit:
                        k, _, v = kv.partition("=")
                        try:
                            limit_changes[k.strip()] = json.loads(v)
                        except ValueError:
                            limit_changes[k.strip()] = v  # refused below, naming the limit
                    limits = RiskLimits().changed(limit_changes) if limit_changes else None
                    acc = store.create_account(
                        s, args.name, strategy_key=args.strategy,
                        symbol=[x.strip() for x in args.symbol.split(",")],
                        timeframe=args.timeframe, source=args.source, capital=args.capital,
                        params=given or None, limits=limits,
                        actor=args.by.strip() or "system", start_now=args.start_now)
                except (KeyError, ValueError, PermissionError, ApexError) as exc:
                    print(f"cannot create {args.name}: {exc}", file=sys.stderr)
                    return 2
                s.commit()
                start = (f", trading prices after {acc.last_bar_ts:%Y-%m-%d %H:%M} UTC"
                         if acc.last_bar_ts else "")
                print(f"created paper account {args.name} (SIMULATED){start}")
                diff = RiskLimits(**acc.limits).differences()
                if diff:
                    print("safety limits for this account only: " + ", ".join(
                        f"{k} {b} (standard {a})" for k, (a, b) in diff.items())
                        + (f"; set by {args.by.strip()}, recorded in the audit log"
                           if args.by.strip() else ""))
            elif args.cmd == "paper-run":
                try:
                    out = store.run_account(s, args.name, now=now, end=args.end)
                except store.AccountHalted as exc:
                    print(f"HALTED: {exc}", file=sys.stderr)
                    return 3
                print("PAPER (SIMULATED) " + " ".join(f"{k}={v}" for k, v in out.items()))
            else:
                store.reset_kill_switch(s, args.name, args.actor, now)
                s.commit()
                print(f"kill switch reset by {args.actor}")
        elif args.cmd == "regimes":
            from apex.domain.market import Timeframe
            from apex.marketdata.repository import get_instrument, load_bars
            from apex.regime.detect import (
                forward_vol_by_state,
                hmm_regime,
                stability,
                trend_regime,
                volatility_regime,
            )

            bars = load_bars(s, get_instrument(s, args.symbol).id, Timeframe(args.timeframe),
                             args.source,
                             args.start, args.end)
            if len(bars) < 100:
                print("need at least 100 stored bars", file=sys.stderr)
                return 2
            train_end = int(len(bars) * args.train_frac)
            state, model = hmm_regime(bars, train_end)
            for name, lab in (("trend", trend_regime(bars)), ("volatility",
                                                             volatility_regime(bars)),
                              ("hmm", state)):
                counts = lab[lab.notna() & (lab != -1)].value_counts(normalize=True)
                st = stability(lab)
                print(f"{name:10} " + " ".join(f"{k}={v:.0%}" for k, v in counts.items())
                      + f"  switch_rate={st['switch_rate']:.3f} mean_run={st['mean_run']:.1f}")
            print("hmm state vol (per bar): " + ", ".join(
                f"{i}={v ** 0.5:.4f}" for i, v in enumerate(model.var_))
                + "  expected durations: " + ", ".join(
                f"{d:.1f}" for d in model.expected_durations()))
            print("OOS mean forward vol by hmm state: "
                  + str(forward_vol_by_state(bars, state, train_end)))
        elif args.cmd == "leaderboard":
            import pandas as pd

            from apex.backtest.tournament import leaderboard

            df = leaderboard(s, asset_class=args.asset_class, symbol=args.symbol,
                             timeframe=args.timeframe, category=args.category)
            if df.empty:
                print("no walk-forward results yet; run `apex tournament`")
            else:
                cols = ["strategy", "status", "symbol", "timeframe", "passed_gates", "score",
                        "deflated_sharpe", "oos_sharpe", "oos_max_drawdown", "pbo",
                        "oos_trades", "family_trials"]
                with pd.option_context("display.width", 200, "display.max_columns", 20):
                    print(df[cols].to_string(index=False))
        elif args.cmd == "shortlist":
            from apex.backtest.tournament import leaderboard
            from apex.shortlist import shortlist, to_markdown

            print(to_markdown(shortlist(leaderboard(s), top=args.top)), end="")
        elif args.cmd == "strategies":
            from apex.strategies.multi import all_multi_strategies
            from apex.strategies.registry import all_strategies

            for key, cls in sorted({**all_strategies(), **all_multi_strategies()}.items()):
                m = cls.meta
                legs = (f" [{cls.legs or '2+'} instruments]" if hasattr(cls, "legs") else "")
                print(f"{key:14} v{m.version:7} {m.category:22} {m.name}{legs}")
        elif args.cmd == "backtest":

            from apex.backtest.engine import BacktestConfig
            from apex.backtest.experiments import run_experiment
            from apex.domain.market import Timeframe
            from apex.strategies.registry import all_strategies

            strategies = all_strategies()
            if args.strategy not in strategies:
                print(f"unknown strategy {args.strategy}; see `apex strategies`", file=sys.stderr)
                return 2
            strat = strategies[args.strategy]()
            if args.regime_filter:
                from apex.regime.filtered import RegimeFiltered

                strat = RegimeFiltered(strat)
            params = {}
            for kv in args.param:
                k, _, v = kv.partition("=")
                params[k] = _param(v) if v not in ("",) else v
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
