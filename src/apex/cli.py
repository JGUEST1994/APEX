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


PROVIDERS = ["synthetic", "csv", "binance", "kraken", "alpaca", "polygon"]


def make_provider(name: str, settings, csv_root: str = "data/csv"):  # noqa: ANN001, ANN201
    if name == "synthetic":
        from apex.marketdata.providers.synthetic import SyntheticProvider

        return SyntheticProvider()
    if name == "csv":
        from apex.marketdata.providers.csv_file import CsvFileProvider

        return CsvFileProvider(Path(csv_root))
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
    if name == "polygon":
        from apex.marketdata.providers.polygon import PolygonStocksProvider

        if not settings.polygon_api_key:
            raise SystemExit("set APEX_POLYGON_API_KEY")
        return PolygonStocksProvider(settings.polygon_api_key.get_secret_value())
    raise SystemExit(f"unknown provider {name}")


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
    va.add_argument("--symbol", required=True)
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
    tn.add_argument("--strategies", default="all", help="comma-separated keys or 'all'")
    tn.add_argument("--symbols", required=True)
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
    pr = sub.add_parser("paper-run", help="process new stored bars for a paper account "
                        "(restart-safe; run from cron or a scheduler)")
    pr.add_argument("--name", required=True)
    pr.add_argument("--end", type=_dt)
    pk = sub.add_parser("paper-reset-kill", help="human reset of a paper account kill switch")
    pk.add_argument("--name", required=True)
    pk.add_argument("--actor", required=True, help="your name (recorded in the audit log)")
    pq = sub.add_parser("paper-compare", help="compare a paper account with a backtest of "
                        "the same strategy over the same bars (read-only)")
    pq.add_argument("--name", required=True)
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
    if args.cmd == "hypotheses":
        from apex.strategies.hypotheses import to_markdown

        print(to_markdown())
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
            provider = make_provider(args.provider, settings, args.csv_root)
            r = ingest_bars(s, provider, spec, Timeframe(args.timeframe), args.start, args.end)
            print(f"run {r.run_id}: {r.status} received={r.received} stored={r.stored}"
                  + (f" issues={len(r.report.issues)}" if r.report else f" error={r.error}"))
            return 0 if r.status != "failed" else 1
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
            import json

            from apex.backtest.experiments import run_validation
            from apex.domain.market import Timeframe
            from apex.strategies.registry import all_strategies

            strategies = all_strategies()
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
            from apex.strategies.registry import all_strategies

            reg = all_strategies()
            keys = sorted(reg) if args.strategies == "all" else args.strategies.split(",")
            missing = [k for k in keys if k not in reg]
            if missing:
                print(f"unknown strategies: {missing}", file=sys.stderr)
                return 2
            entries = run_tournament(
                s, [reg[k]() for k in keys], symbols=args.symbols.split(","),
                timeframe=Timeframe(args.timeframe), source=args.source, start=args.start,
                end=args.end, train_bars=args.train_bars, test_bars=args.test_bars,
                promote=not args.no_promote, regime_filter=args.regime_filter)
            print("TOURNAMENT (SIMULATED, out-of-sample, trial-adjusted)")
            for e in entries:
                verdict = "PASS" if e.passed else "fail"
                print(f"{verdict:4} {e.score:6.3f} {e.strategy_key:14} {e.symbol:8} "
                      f"{e.timeframe:4} {'; '.join(e.reasons)[:110]}")
        elif args.cmd == "backtest-pair":
            import json

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
                params[k] = json.loads(v)
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
            import json

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
        elif args.cmd in ("paper-create", "paper-run", "paper-reset-kill"):
            from datetime import UTC, datetime

            from apex.core.config import TradingMode
            from apex.execution import store

            if settings.trading_mode is not TradingMode.PAPER:
                print("paper commands need APEX_TRADING_MODE=paper", file=sys.stderr)
                return 2
            now = datetime.now(UTC)
            if args.cmd == "paper-create":
                store.create_account(s, args.name, strategy_key=args.strategy,
                                     symbol=[x.strip() for x in args.symbol.split(",")],
                                     timeframe=args.timeframe,
                                     source=args.source, capital=args.capital)
                s.commit()
                print(f"created paper account {args.name} (SIMULATED)")
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
        elif args.cmd == "strategies":
            from apex.strategies.multi import all_multi_strategies
            from apex.strategies.registry import all_strategies

            for key, cls in sorted({**all_strategies(), **all_multi_strategies()}.items()):
                m = cls.meta
                legs = f" [{cls.legs} instruments]" if hasattr(cls, "legs") else ""
                print(f"{key:14} v{m.version:7} {m.category:22} {m.name}{legs}")
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
            if args.regime_filter:
                from apex.regime.filtered import RegimeFiltered

                strat = RegimeFiltered(strat)
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
