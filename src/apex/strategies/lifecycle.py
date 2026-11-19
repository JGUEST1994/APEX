"""Persisted strategy status transitions. Every change is audited; illegal moves raise."""

from __future__ import annotations

from sqlalchemy.orm import Session

from apex.db.models import AuditEvent, StrategyStatusChange, StrategyVersion
from apex.strategies.base import StrategyStatus, can_transition


class IllegalTransition(ValueError):
    pass


def transition(session: Session, version: StrategyVersion, to: StrategyStatus, *,
               reason: str, actor: str = "system") -> StrategyVersion:
    src = StrategyStatus(version.status)
    if src == to:
        return version
    if not can_transition(src, to):
        raise IllegalTransition(f"{src.value} -> {to.value} is not allowed")
    if to is StrategyStatus.APPROVED and actor == "system":
        raise IllegalTransition("only a human may approve a strategy")
    version.status = to.value
    session.add(StrategyStatusChange(strategy_version_id=version.id, from_status=src.value,
                                     to_status=to.value, reason=reason, actor=actor))
    session.add(AuditEvent(actor=actor, action="strategy_status",
                           target=f"strategy_version:{version.id}",
                           detail={"from": src.value, "to": to.value, "reason": reason}))
    session.flush()
    return version
