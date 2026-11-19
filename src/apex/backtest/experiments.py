"""Reproducible experiment runs: exact data hash, strategy version, code commit, params."""

from __future__ import annotations

import hashlib
import inspect
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
    code_sha = hashlib.sha256(inspect.getsource(type(strategy)).encode()).hexdigest()
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
