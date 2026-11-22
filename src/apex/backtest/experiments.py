"""Reproducible experiment runs: exact data hash, strategy version, code commit, params."""

from __future__ import annotations

import hashlib
import math
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from apex.db.models import Experiment, StrategyStatusChange, StrategyVersion
from apex.db.models import Strategy as StrategyRow
from apex.domain.market import Timeframe
from apex.marketdata.repository import frame_sha256, get_instrument, load_bars, to_spec
from apex.strategies.base import Strategy, StrategyStatus
from apex.strategies.multi import MultiStrategy


def code_version() -> str:
    try:
        root = Path(__file__).resolve().parents[3]
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                             text=True, timeout=5, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               cwd=root, capture_output=True, text=True, timeout=5).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except Exception:  # noqa: BLE001 - not a git checkout (e.g. installed package)
        from apex import __version__

        return f"package-{__version__}"


def ensure_strategy_version(session: Session, strategy: Strategy) -> StrategyVersion:
    meta = strategy.meta
    row = session.scalar(select(StrategyRow).where(StrategyRow.key == meta.key))
    if row is None:
        row = StrategyRow(key=meta.key, name=meta.name, category=meta.category,
                          description=meta.description)
        session.add(row)
        session.flush()
    code_sha = hashlib.sha256(strategy.source_code().encode()).hexdigest()
    ver = session.scalar(select(StrategyVersion).where(
        StrategyVersion.strategy_id == row.id, StrategyVersion.version == meta.version))
    if ver is None:
        spec = {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(meta).items()}
        spec["asset_classes"] = [a.value for a in meta.asset_classes]
        spec["timeframes"] = [t.value for t in meta.timeframes]
        ver = StrategyVersion(strategy_id=row.id, version=meta.version, spec=spec,
                              code_sha256=code_sha, status=StrategyStatus.EXPERIMENTAL.value)
        session.add(ver)
        session.flush()
        session.add(StrategyStatusChange(strategy_version_id=ver.id, from_status=None,
                                         to_status=ver.status, reason="registered"))
    elif ver.code_sha256 != code_sha:
        raise ValueError(
            f"{meta.key} v{meta.version}: code changed since this version was registered; "
            "bump StrategyMeta.version so results stay attributable")
    return ver


def run_experiment(session: Session, strategy: Strategy, *, symbol: str, timeframe: Timeframe,
                   source: str, start: datetime, end: datetime,
                   params: dict[str, Any] | None = None, config: BacktestConfig | None = None,
                   venue: str | None = None) -> tuple[Experiment, BacktestResult]:
    inst = get_instrument(session, symbol, venue)
    bars = load_bars(session, inst.id, timeframe, source, start, end)
    if len(bars) < 2:
        raise ValueError(f"not enough {source} {timeframe.value} bars for {symbol} in range")
    ver = ensure_strategy_version(session, strategy)
    cfg = config or BacktestConfig()
    full_params = strategy.params(params)
    result = run_backtest(bars, strategy, to_spec(inst), full_params, cfg)
    exp = Experiment(
        strategy_version_id=ver.id, kind="backtest", params=full_params,
        data_manifest={"symbol": symbol, "venue": inst.venue, "timeframe": timeframe.value,
                       "source": source, "start": start.isoformat(), "end": end.isoformat(),
                       "bars": len(bars), "first_ts": bars["ts"].iloc[0].isoformat(),
                       "last_ts": bars["ts"].iloc[-1].isoformat(),
                       "data_sha256": frame_sha256(bars), "config": asdict(cfg)},
        code_version=code_version(), random_seed=None, status="done",
        metrics=result.metrics, finished_at=datetime.now(UTC))
    session.add(exp)
    session.flush()
    return exp, result


def run_pair_experiment(session: Session, strategy: MultiStrategy, *, symbols: list[str],
                        timeframe: Timeframe, source: str, start: datetime, end: datetime,
                        params: dict[str, Any] | None = None,
                        config: BacktestConfig | None = None
                        ) -> tuple[Experiment, BacktestResult]:
    """Backtest a multi-instrument strategy and record it like any other experiment."""
    from apex.backtest.portfolio import run_portfolio_backtest

    bars, specs, hashes = _load_legs(session, symbols, timeframe, source, start, end)
    ver = ensure_strategy_version(session, strategy)
    cfg = config or BacktestConfig()
    full_params = strategy.params(params)
    result = run_portfolio_backtest(bars, strategy, specs, full_params, cfg)
    exp = Experiment(
        strategy_version_id=ver.id, kind="backtest", params=full_params,
        data_manifest={"symbol": "/".join(symbols), "symbols": symbols,
                       "timeframe": timeframe.value, "source": source,
                       "start": start.isoformat(), "end": end.isoformat(),
                       "bars": int(len(result.equity)),
                       "data_sha256": _combined_sha(hashes, symbols),
                       "data_sha256_by_symbol": hashes, "config": asdict(cfg)},
        code_version=code_version(), random_seed=None, status="done",
        metrics=result.metrics, finished_at=datetime.now(UTC))
    session.add(exp)
    session.flush()
    return exp, result


def _load_legs(session: Session, symbols: list[str], timeframe: Timeframe, source: str,
               start: datetime, end: datetime) -> tuple[dict, dict, dict]:
    bars, specs, hashes = {}, {}, {}
    for sym in symbols:
        inst = get_instrument(session, sym)
        b = load_bars(session, inst.id, timeframe, source, start, end)
        if len(b) < 2:
            raise ValueError(f"not enough {source} {timeframe.value} bars for {sym} in range")
        bars[sym], specs[sym], hashes[sym] = b, to_spec(inst), frame_sha256(b)
    return bars, specs, hashes


def _combined_sha(hashes: dict[str, str], symbols: list[str]) -> str:
    return hashlib.sha256("|".join(hashes[s] for s in symbols).encode()).hexdigest()


def run_validation(session: Session, strategy: Strategy, *, symbol: str, timeframe: Timeframe,
                   source: str, start: datetime, end: datetime, grid: dict[str, list],
                   train_bars: int, test_bars: int, anchored: bool = False,
                   config: BacktestConfig | None = None, mc_runs: int = 2000, seed: int = 7,
                   venue: str | None = None) -> Experiment:
    """Walk-forward + PBO + Monte Carlo, recorded as one reproducible experiment."""
    from apex.backtest.validation import (
        monte_carlo_trades,
        param_grid,
        probability_of_backtest_overfitting,
        returns_matrix,
        walk_forward,
    )

    if isinstance(strategy, MultiStrategy):  # symbol is "A,B" or "A/B" in leg order
        symbols = [x.strip() for x in symbol.replace("/", ",").split(",") if x.strip()]
        bars, spec, hashes = _load_legs(session, symbols, timeframe, source, start, end)
        manifest = {"symbol": "/".join(symbols), "symbols": symbols, "venue": None,
                    "data_sha256": _combined_sha(hashes, symbols),
                    "data_sha256_by_symbol": hashes,
                    "bars": min(len(b) for b in bars.values())}
    else:
        inst = get_instrument(session, symbol, venue)
        spec = to_spec(inst)
        bars = load_bars(session, inst.id, timeframe, source, start, end)
        manifest = {"symbol": symbol, "venue": inst.venue, "bars": len(bars),
                    "data_sha256": frame_sha256(bars)}
    cfg = config or BacktestConfig()
    wf = walk_forward(bars, strategy, spec, grid, train_bars=train_bars,
                      test_bars=test_bars, anchored=anchored, config=cfg)
    metrics: dict[str, Any] = dict(wf.summary)
    # The overfitting check compares settings with each other, so a single setting skips
    # it (and the full-history backtest it would need).
    if len(param_grid(grid)) >= 2:
        matrix = returns_matrix(bars, strategy, spec, grid, cfg)
        if matrix.shape[1] >= 2:
            metrics["pbo"] = probability_of_backtest_overfitting(matrix)["pbo"]
    oos_eq = cfg.initial_capital * (1 + wf.oos_returns).cumprod()
    pnl = oos_eq.diff().dropna()
    metrics["monte_carlo_bar_bootstrap"] = monte_carlo_trades(
        pnl[pnl != 0].tolist(), cfg.initial_capital, n=mc_runs, seed=seed)
    metrics["folds_detail"] = [
        {"test_start": f.test_start.isoformat(), "test_end": f.test_end.isoformat(),
         "params": f.params, "is_score": f.is_score if math.isfinite(f.is_score) else None,
         "oos": f.oos_metrics} for f in wf.folds]
    ver = ensure_strategy_version(session, strategy)
    exp = Experiment(
        strategy_version_id=ver.id, kind="walk_forward",
        params={"grid": grid, "train_bars": train_bars, "test_bars": test_bars,
                "anchored": anchored},
        data_manifest={**manifest, "timeframe": timeframe.value, "source": source,
                       "start": start.isoformat(), "end": end.isoformat(),
                       "config": asdict(cfg)},
        code_version=code_version(), random_seed=seed, status="done", metrics=metrics,
        finished_at=datetime.now(UTC))
    session.add(exp)
    session.flush()
    return exp


class ReplayError(ValueError):
    """The experiment cannot be redrawn exactly (wrong kind, code or data changed)."""


def replay_experiment(session: Session, exp: Experiment) -> BacktestResult:
    """Re-run a recorded backtest exactly: same strategy version, parameters, costs and
    stored bars (checked against the recorded data hash). Read-only: records nothing."""
    from apex.backtest.portfolio import run_portfolio_backtest
    from apex.strategies.multi import all_multi_strategies
    from apex.strategies.registry import all_strategies

    if exp.kind != "backtest":
        raise ReplayError("only plain backtests can be redrawn")
    ver = session.get(StrategyVersion, exp.strategy_version_id)
    key = session.get(StrategyRow, ver.strategy_id).key
    base = key.removesuffix("+RF")
    cls = {**all_strategies(), **all_multi_strategies()}.get(base)
    if cls is None:
        raise ReplayError(f"strategy {key} is no longer registered")
    strategy = cls()
    if key.endswith("+RF"):
        from apex.regime.filtered import RegimeFiltered

        strategy = RegimeFiltered(strategy)
    code_sha = hashlib.sha256(strategy.source_code().encode()).hexdigest()
    if strategy.meta.version != ver.version or code_sha != ver.code_sha256:
        raise ReplayError(f"{key} has changed since this backtest (it ran v{ver.version}); "
                          "run it again to see a chart")
    m = exp.data_manifest or {}
    tf = Timeframe(m["timeframe"])
    start, end = datetime.fromisoformat(m["start"]), datetime.fromisoformat(m["end"])
    cfg = BacktestConfig(**(m.get("config") or {}))
    if "symbols" in m:
        bars, specs, hashes = _load_legs(session, m["symbols"], tf, m["source"], start, end)
        if _combined_sha(hashes, m["symbols"]) != m.get("data_sha256"):
            raise ReplayError("the stored prices have changed since this backtest")
        return run_portfolio_backtest(bars, strategy, specs, exp.params, cfg)
    inst = get_instrument(session, m["symbol"], m.get("venue"))
    bars = load_bars(session, inst.id, tf, m["source"], start, end)
    if len(bars) < 2 or frame_sha256(bars) != m.get("data_sha256"):
        raise ReplayError("the stored prices have changed since this backtest")
    return run_backtest(bars, strategy, to_spec(inst), exp.params, cfg)
