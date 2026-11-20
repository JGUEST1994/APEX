"""`apex quickstart`: from an empty folder to a first backtest in one command.

Sets up the database, adds the default markets, downloads about three years of free daily
prices (Coinbase Bitcoin by default; only the missing bars, so it is safe to run again),
runs one simple example strategy over them and says where to look next. Everything it
produces is simulated research; it never trades.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from apex.core.config import Settings

EXAMPLE_STRATEGY = "TF-SMA-001"


def run(settings: Settings, provider_name: str = "coinbase", symbol: str = "BTCUSDT",
        years: int = 3, out: Callable[[str], None] = print,
        now: datetime | None = None, provider: object | None = None) -> int:
    from apex.backtest.experiments import run_experiment
    from apex.db.migrate import upgrade
    from apex.db.session import make_engine, make_session_factory, session_scope
    from apex.domain.instruments import DEFAULT_UNIVERSE, by_symbol
    from apex.domain.market import Timeframe
    from apex.marketdata.ingest import update_bars
    from apex.marketdata.repository import latest_bar_ts, upsert_instrument
    from apex.strategies.registry import all_strategies

    now = now or datetime.now(UTC)
    spec = by_symbol().get(symbol)
    if spec is None:
        out(f"Unknown market {symbol}. See the Markets list in docs/USER_GUIDE.md.")
        return 2

    out("1/4 Setting up the database...")
    upgrade(settings.database_url)
    engine = make_engine(settings.database_url)
    try:
        factory = make_session_factory(engine)
        with session_scope(factory) as s:
            out(f"2/4 Adding the {len(DEFAULT_UNIVERSE)} standard markets...")
            for m in DEFAULT_UNIVERSE:
                upsert_instrument(s, m)
            s.commit()

            if provider is None:
                from apex.cli import make_provider

                provider = make_provider(provider_name, settings)
            out(f"3/4 Getting daily {symbol} prices from {provider.name} (only what is "
                "missing)...")
            start = now - timedelta(days=365 * years)
            r = update_bars(s, provider, spec, Timeframe.D1, backfill_start=start, now=now)
            s.commit()
            if r is not None and r.status == "failed":
                out(f"    The download failed: {r.error}. Check the internet connection "
                    "and run apex quickstart again.")
                return 1
            out("    Up to date already." if r is None else f"    Saved {r.stored} new days.")
            inst = upsert_instrument(s, spec)
            last = latest_bar_ts(s, inst.id, Timeframe.D1, provider.name)
            if last is None:
                out("    No prices came back, so there is nothing to test yet.")
                return 1

            out(f"4/4 Testing the example idea {EXAMPLE_STRATEGY} (hold it while the 20-day "
                "average is above the 50-day one, bet against it while below)...")
            exp, res = run_experiment(s, all_strategies()[EXAMPLE_STRATEGY](), symbol=symbol,
                                      timeframe=Timeframe.D1, source=provider.name,
                                      start=start, end=last + timedelta(days=1))
            s.commit()
            m = res.metrics
            out(f"    Backtest {exp.id} (simulated): total return "
                f"{m.get('total_return', 0):.1%}, worst drop {m.get('max_drawdown', 0):.1%}, "
                f"{m.get('trades', 0)} trades.")
    finally:
        engine.dispose()
    out("")
    out("Done. Next: run  apex serve  and open http://127.0.0.1:8000/ui/  (Backtests tab).")
    out("This was one quick test on past prices, not a recommendation. See 'Rank ideas "
        "fairly' in docs/USER_GUIDE.md before trusting any result.")
    return 0
