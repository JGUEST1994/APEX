"""The practice ("paper") record a strategy needs before a person may consider approving it.

Roadmap gate for Phase 9: "30 days continuous paper running with no unhandled errors".
Replaying years of stored history through a new account proves nothing about running
forward, so only bars dated *after the account was created* count. Read-only: it reports,
it never changes a strategy's status (approval is always a human decision).

A record is ready when all of these hold:

- at least ``MIN_DAYS`` calendar days of bars processed since the account was created;
- the account is still running: not halted, kill switch not engaged, and its newest bar is
  no older than ``STALE_DAYS`` (so a stopped scheduler shows up);
- no kill-switch trip and no halt (reconciliation mismatch) since it was created;
- the paper-vs-backtest report says paper and backtest agree.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.db.models import AuditEvent, PaperAccount
from apex.execution.consistency import compare

MIN_DAYS = 30
STALE_DAYS = 4  # a long weekend of closed markets is fine; a week is not
PROBLEM_ACTIONS = ("kill_switch.engage", "paper_account.halt")


def record(session: Session, name: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    acc = session.scalar(select(PaperAccount).where(PaperAccount.name == name))
    if acc is None:
        raise KeyError(name)
    start, last = acc.created_at, acc.last_bar_ts
    days = max((last - start).total_seconds() / 86400, 0.0) if last else 0.0
    events = session.scalars(
        select(AuditEvent).where(AuditEvent.target == name,
                                 AuditEvent.action.in_(PROBLEM_ACTIONS),
                                 AuditEvent.at >= start).order_by(AuditEvent.at)).all()
    stale = last is None or now - last > timedelta(days=STALE_DAYS)
    running = acc.status == "running" and not acc.kill_engaged and not stale
    cons = compare(session, name)

    reasons = []
    if days < MIN_DAYS:
        reasons.append(f"only {days:.0f} of {MIN_DAYS} days of practice trading since the "
                       f"account was created on {start:%Y-%m-%d} (older history replayed "
                       "into it does not count)")
    if acc.status != "running":
        reasons.append("the account is halted after a reconciliation problem")
    if acc.kill_engaged:
        reasons.append(f"the kill switch is engaged ({acc.kill_reason})")
    if stale:
        reasons.append("no new prices processed for more than "
                       f"{STALE_DAYS} days; is the scheduler running?")
    if events:
        reasons.append(f"{len(events)} kill-switch or halt event(s) since it started")
    if cons.get("consistent") is not True:
        reasons.append("practice trades and the backtest do not agree yet "
                       f"({'; '.join(cons.get('notes') or []) or 'not enough data'})")
    return {
        "account": name, "strategy": acc.strategy_key, "symbols": acc.all_symbols,
        "simulated": True, "started": start.isoformat(),
        "last_bar": last.isoformat() if last else None,
        "days": round(days, 1), "min_days": MIN_DAYS, "running": running,
        "problem_events": [{"at": e.at.isoformat(), "action": e.action,
                            "detail": e.detail} for e in events],
        "consistent": cons.get("consistent"),
        "direction_agreement": cons.get("direction_agreement"),
        "ready": not reasons, "reasons": reasons,
    }
