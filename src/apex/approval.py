"""A named person approves a strategy, only once its practice record is ready.

Jake's decision (2026-11-20): approval is a human step, allowed only when
``apex paper-record`` says READY for a practice account running that strategy. The
strategy must already have passed validation (the tournament gate moves it to
``validation``); its code must be unchanged since that version was registered. Every step is
written to the strategy's status history and the audit log. Approval enables nothing by
itself: APEX has no real-money trading.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from apex.backtest.experiments import ensure_strategy_version
from apex.db.models import StrategyVersion
from apex.paper_record import record
from apex.strategies.base import StrategyStatus
from apex.strategies.lifecycle import transition
from apex.strategies.multi import all_multi_strategies
from apex.strategies.registry import all_strategies

NOT_A_PERSON = ("", "system", "risk", "scheduler", "apex")


class ApprovalRefused(ValueError):
    pass


def approve(session: Session, account: str, *, by: str, reason: str = "",
            now: datetime | None = None) -> StrategyVersion:
    who = by.strip()
    if who.lower() in NOT_A_PERSON:
        raise PermissionError("a strategy can only be approved by a named person (--by)")
    rec = record(session, account, now)
    if not rec["ready"]:
        raise ApprovalRefused("the practice record is not ready: " + "; ".join(rec["reasons"]))
    cls = {**all_strategies(), **all_multi_strategies()}.get(rec["strategy"])
    if cls is None:
        raise ApprovalRefused(f"{rec['strategy']} is not a registered strategy")
    try:
        ver = ensure_strategy_version(session, cls())
    except ValueError as exc:  # code changed since the version was registered
        raise ApprovalRefused(str(exc)) from None
    status = StrategyStatus(ver.status)
    if status is StrategyStatus.APPROVED:
        raise ApprovalRefused(f"{rec['strategy']} v{ver.version} is already approved")
    if status not in (StrategyStatus.VALIDATION, StrategyStatus.PAPER_TRADING):
        raise ApprovalRefused(f"{rec['strategy']} v{ver.version} is '{status.value}': it must "
                              "pass the tournament's validation gate before it can be "
                              "approved")
    evidence = (f"practice account {account}: {rec['days']} days since "
                f"{rec['started'][:10]}, consistent with its backtest, no kill-switch trips")
    if status is StrategyStatus.VALIDATION:
        transition(session, ver, StrategyStatus.PAPER_TRADING, reason=evidence)
    return transition(session, ver, StrategyStatus.APPROVED, actor=who,
                      reason=(reason.strip() + "; " if reason.strip() else "") + evidence)
