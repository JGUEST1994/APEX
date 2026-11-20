"""A short, plain-English shortlist from the tournament leaderboard (read-only).

Answers "which strategies are worth a 30-day practice run, and why did the rest fail?".
Results that passed every gate come first, best robustness score first. If fewer than
``top`` passed, the nearest misses follow (fewest failed tests, then the highest confidence
that the edge is real), clearly marked as not passing: they are for interest, not for
practice runs. Every figure is simulated and out-of-sample; none of it is advice.
"""

from __future__ import annotations

import math
from collections import Counter

import pandas as pd

from apex.backtest.tournament import Gates

_BAR = Gates().min_dsr  # the confidence a result needs to pass


def _pc(x: object, signed: bool = False) -> str:
    if not isinstance(x, int | float) or not math.isfinite(x):
        return "n/a"
    return f"{x:+.0%}" if signed else f"{x:.0%}"


def _conf(x: object) -> str:
    """Confidence as a whole percent, or with one decimal when rounding would put it on the
    other side of the bar (94.8% must not read as 95%)."""
    if isinstance(x, int | float) and math.isfinite(x) and (x < _BAR) != (round(x, 2) < _BAR):
        return f"{x:.1%}"
    return _pc(x)


def _n(k: int, one: str, many: str) -> str:
    return f"{k} {one if k == 1 else many}"


def _reasons(text: object) -> list[str]:
    return [r.strip() for r in str(text or "").split(";") if r.strip()]


def _short(reason: str) -> str:
    """The plain part before the figures: 'could be luck: confidence 41%...' -> 'could be luck'."""
    return reason.split(":", 1)[0].strip()


def _line(r: pd.Series) -> str:
    folds = r.get("pct_profitable_folds")
    trades = int(r["oos_trades"]) if pd.notna(r.get("oos_trades")) else 0
    parts = [f"confidence the edge is real {_conf(r.get('deflated_sharpe'))}",
             f"return on unseen data {_pc(r.get('oos_return'), signed=True)}",
             f"worst drop {_pc(r.get('oos_max_drawdown'))}",
             f"{trades} trades on unseen data"]
    if folds is not None and pd.notna(folds):
        parts.insert(1, f"profitable in {_pc(folds)} of unseen stretches")
    held = r.get("buy_hold_return")
    if held is not None and pd.notna(held):  # the benchmark: simply holding the market
        parts.append(f"simply holding it over the same stretches: "
                     f"{_pc(held, signed=True)}, worst drop {_pc(r.get('buy_hold_max_drawdown'))}")
    return f"**{r['strategy']}** on {r['symbol']} ({r['timeframe']}): " + ", ".join(parts)


def shortlist(df: pd.DataFrame, top: int = 5) -> dict:
    """Pick the shortlist from a :func:`apex.backtest.tournament.leaderboard` frame."""
    if df.empty:
        return {"tested": 0, "strategies": 0, "markets": 0, "passed": [], "near_misses": [],
                "why_failed": {}}
    passed = df[df["passed_gates"]].sort_values("score", ascending=False)
    failed = df[~df["passed_gates"]].copy()
    failed["n_failed"] = failed["fail_reasons"].map(lambda t: len(_reasons(t)))
    failed["dsr_sort"] = pd.to_numeric(failed["deflated_sharpe"], errors="coerce").fillna(-1.0)
    # a result that never traded on unseen data is not a near miss, whatever its figures say
    traded = pd.to_numeric(failed["oos_trades"], errors="coerce").fillna(0) > 0
    near = failed[traded].sort_values(["n_failed", "dsr_sort"], ascending=[True, False])
    why = Counter(_short(r) for t in failed["fail_reasons"] for r in _reasons(t))
    return {
        "tested": len(df), "strategies": df["strategy"].nunique(),
        "markets": df["symbol"].nunique(),
        "passed": [_line(r) for _, r in passed.head(top).iterrows()],
        "n_passed": len(passed),
        "near_misses": [f"{_line(r)}. Missed: {r['fail_reasons']}"
                        for _, r in near.head(max(0, top - len(passed))).iterrows()],
        "why_failed": dict(why.most_common()),
        "n_failed": len(failed),
    }


def to_markdown(sl: dict) -> str:
    if not sl["tested"]:
        return ("No tournament results yet. Run `apex tournament` (or "
                "scripts/research-run.sh) first.\n")
    out = ["# APEX shortlist (simulated research results, not advice)", "",
           f"Tested {sl['tested']} strategy x market x timeframe combinations "
           f"({_n(sl['strategies'], 'strategy', 'strategies')}, "
           f"{_n(sl['markets'], 'market', 'markets')}). "
           f"Passed every test: {sl['n_passed']}.", ""]
    out.append("## Worth a 30-day practice run")
    out += [f"{i}. {s}" for i, s in enumerate(sl["passed"], 1)] or [
        "None passed every test this time. That is a normal result: most trading ideas do "
        "not survive fair testing, and APEX is built to say so rather than flatter them."]
    if sl["near_misses"]:
        out += ["", "## Nearest misses (did not pass; for interest only, not for practice runs)"]
        out += [f"{i}. {s}" for i, s in enumerate(sl["near_misses"], 1)]
    if sl["why_failed"]:
        which = "the other " if sl["n_passed"] else "all "
        out += ["", f"## Why {which}{sl['n_failed']} failed (one result can fail several tests)"]
        out += [f"- {k}: {v}" for k, v in sl["why_failed"].items()]
    out += ["", "Every figure is from simulated trading on data each strategy never saw while "
            "being tuned, after allowing for how many ideas were tried. Past results do not "
            "guarantee future ones."]
    return "\n".join(out) + "\n"
