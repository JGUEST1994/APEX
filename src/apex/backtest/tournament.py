"""Strategy tournament and leaderboard (spec Section 8, design 06).

Strategies are ranked on out-of-sample, trial-adjusted evidence, never on raw historical
return. Automatic promotion stops at ``validation``: paper trading needs the Phase 9
engine and ``approved`` always needs a human.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apex.backtest.experiments import run_validation
from apex.backtest.validation import expected_max_sharpe, probabilistic_sharpe
from apex.db.models import Experiment, StrategyVersion
from apex.db.models import Strategy as StrategyRow
from apex.domain.market import Timeframe
from apex.strategies.base import Strategy, StrategyStatus
from apex.strategies.lifecycle import transition


@dataclass(frozen=True)
class Gates:
    """Promotion gates. Initial values from design 06; tune with evidence, never loosen
    to rescue a favourite strategy."""

    min_oos_trades: int = 100
    min_dsr: float = 0.95
    min_profitable_folds: float = 0.6
    max_drawdown: float = 0.25  # risk profile: worst acceptable OOS drawdown
    max_pbo: float = 0.5


@dataclass
class Entry:
    strategy_key: str
    version: str
    category: str
    symbol: str
    timeframe: str
    experiment_id: int
    metrics: dict
    family_trials: int
    passed: bool
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0


def family_trials(session: Session, strategy_key: str) -> int:
    """All walk-forward trials ever recorded for this strategy key (every version)."""
    rows = session.scalars(
        select(Experiment.metrics).join(StrategyVersion)
        .join(StrategyRow, StrategyRow.id == StrategyVersion.strategy_id)
        .where(StrategyRow.key == strategy_key, Experiment.kind == "walk_forward")).all()
    return int(sum((m or {}).get("n_trials", 0) for m in rows))


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, int | float) and math.isfinite(x) else None


def evaluate(metrics: dict, gates: Gates, family_n: int) -> tuple[bool, list[str], dict]:
    """Apply gates; DSR is recomputed with the family-wide trial count."""
    m = dict(metrics)
    reasons = []
    oos_trades = sum((f.get("oos") or {}).get("trades") or 0
                     for f in m.get("folds_detail", []))
    m["oos_trades"] = oos_trades
    dsr = _num(m.get("deflated_sharpe"))
    inp = m.get("dsr_inputs")
    if inp and family_n > m.get("n_trials", 0):
        # re-deflate against the expected maximum Sharpe of ALL trials in the family
        sr0 = expected_max_sharpe(family_n, inp["sr_variance"])
        dsr = _num(probabilistic_sharpe(inp["sharpe_per_period"], inp["n_obs"], inp["skew"],
                                        inp["kurtosis"], sr0))
    m["deflated_sharpe_family"] = dsr
    if oos_trades < gates.min_oos_trades:
        reasons.append(f"only {oos_trades} OOS trades (< {gates.min_oos_trades})")
    if dsr is None or dsr < gates.min_dsr:
        reasons.append(f"deflated Sharpe {dsr if dsr is None else round(dsr, 3)} < {gates.min_dsr}")
    pf = _num(m.get("pct_profitable_folds"))
    if pf is None or pf < gates.min_profitable_folds:
        reasons.append(f"profitable folds {pf} < {gates.min_profitable_folds}")
    dd = _num(m.get("oos_max_drawdown"))
    if dd is None or dd < -gates.max_drawdown:
        reasons.append(f"OOS drawdown {dd} worse than -{gates.max_drawdown}")
    pbo = _num(m.get("pbo"))
    if pbo is not None and pbo > gates.max_pbo:
        reasons.append(f"PBO {round(pbo, 3)} > {gates.max_pbo}")
    return not reasons, reasons, m


def score(m: dict) -> float:
    """Composite robustness score in [0, 1]. Raw return deliberately has no weight."""
    dsr = _num(m.get("deflated_sharpe_family")) or 0.0
    pf = _num(m.get("pct_profitable_folds")) or 0.0
    dd = abs(_num(m.get("oos_max_drawdown")) or 1.0)
    pbo = _num(m.get("pbo"))
    stab = _num(m.get("param_stability")) or 0.0
    return round(0.4 * dsr + 0.2 * pf + 0.15 * max(0.0, 1 - dd / 0.5)
                 + 0.15 * (1 - pbo if pbo is not None else 0.5) + 0.1 * stab, 6)


def run_tournament(session: Session, strategies: list[Strategy], *, symbols: list[str],
                   timeframe: Timeframe, source: str, start: datetime, end: datetime,
                   grids: dict[str, dict[str, list]] | None = None, train_bars: int,
                   test_bars: int, gates: Gates | None = None,
                   promote: bool = True, regime_filter: bool = False) -> list[Entry]:
    """Validate and rank ``strategies``. With ``regime_filter`` every strategy that declares
    specific regimes also competes as a ``<KEY>+RF`` variant; both variants share one trial
    family, so trying the filter counts against the deflated Sharpe of each."""
    gates = gates or Gates()
    entries: list[Entry] = []
    if regime_filter:
        from apex.regime.filtered import RegimeFiltered

        strategies = list(strategies) + [RegimeFiltered(s) for s in strategies
                                         if "any" not in s.meta.regimes]
    for strat in strategies:
        base_key = strat.meta.key.removesuffix("+RF")
        grid = (grids or {}).get(base_key) or {k: [v] for k, v in
                                                      strat.meta.default_params.items()}
        for sym in symbols:
            try:
                exp = run_validation(session, strat, symbol=sym, timeframe=timeframe,
                                     source=source, start=start, end=end, grid=grid,
                                     train_bars=train_bars, test_bars=test_bars)
            except ValueError as exc:
                entries.append(Entry(strat.meta.key, strat.meta.version, strat.meta.category,
                                     sym, timeframe.value, -1, {}, 0, False, [str(exc)]))
                continue
            fam = family_trials(session, base_key) + family_trials(session, base_key + "+RF")
            ok, reasons, m = evaluate(exp.metrics, gates, fam)
            e = Entry(strat.meta.key, strat.meta.version, strat.meta.category, sym,
                      timeframe.value, exp.id, m, fam, ok, reasons, score(m) if ok else 0.0)
            entries.append(e)
            if promote:
                _promote(session, exp.strategy_version_id, e)
    entries.sort(key=lambda e: (e.passed, e.score), reverse=True)
    return entries


def _promote(session: Session, version_id: int, e: Entry) -> None:
    ver = session.get(StrategyVersion, version_id)
    status = StrategyStatus(ver.status)
    if status is StrategyStatus.EXPERIMENTAL:
        transition(session, ver, StrategyStatus.BACKTESTING,
                   reason=f"tournament run, experiment {e.experiment_id}")
        status = StrategyStatus.BACKTESTING
    if status is StrategyStatus.BACKTESTING and e.passed:
        transition(session, ver, StrategyStatus.VALIDATION,
                   reason=f"passed gates on {e.symbol} {e.timeframe} "
                          f"(experiment {e.experiment_id}, score {e.score})")


def leaderboard(session: Session, *, asset_class: str | None = None,
                symbol: str | None = None, timeframe: str | None = None,
                category: str | None = None, gates: Gates | None = None) -> pd.DataFrame:
    """Latest walk-forward result per (strategy version, symbol, timeframe), ranked."""
    from apex.db.models import Instrument

    gates = gates or Gates()
    latest = (select(func.max(Experiment.id)).where(Experiment.kind == "walk_forward")
              .group_by(Experiment.strategy_version_id,
                        Experiment.data_manifest["symbol"].as_string(),
                        Experiment.data_manifest["timeframe"].as_string()))
    rows = session.execute(
        select(Experiment, StrategyVersion, StrategyRow).select_from(Experiment)
        .join(StrategyVersion, Experiment.strategy_version_id == StrategyVersion.id)
        .join(StrategyRow, StrategyRow.id == StrategyVersion.strategy_id)
        .where(Experiment.id.in_(latest))).all()
    asset_by_symbol = dict(session.execute(select(Instrument.symbol, Instrument.asset_class)).all())
    out = []
    for exp, ver, srow in rows:
        dm = exp.data_manifest
        fam = family_trials(session, srow.key)
        ok, reasons, m = evaluate(exp.metrics, gates, fam)
        out.append({
            "strategy": srow.key, "version": ver.version, "status": ver.status,
            "category": srow.category, "symbol": dm.get("symbol"),
            "asset_class": asset_by_symbol.get(dm.get("symbol")),
            "timeframe": dm.get("timeframe"), "experiment_id": exp.id,
            "score": score(m) if ok else 0.0, "passed_gates": ok,
            "deflated_sharpe": m.get("deflated_sharpe_family"),
            "oos_sharpe": m.get("mean_oos_sharpe"), "oos_return": m.get("oos_total_return"),
            "oos_max_drawdown": m.get("oos_max_drawdown"), "pbo": m.get("pbo"),
            "oos_trades": m.get("oos_trades"), "family_trials": fam,
            "fail_reasons": "; ".join(reasons), "simulated": True})
    df = pd.DataFrame(out)
    if df.empty:
        return df
    for col, val in (("asset_class", asset_class), ("symbol", symbol),
                     ("timeframe", timeframe), ("category", category)):
        if val:
            df = df[df[col] == val]
    return df.sort_values(["passed_gates", "score", "deflated_sharpe"],
                          ascending=False, na_position="last").reset_index(drop=True)
