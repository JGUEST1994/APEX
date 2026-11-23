"""Economic calendar and macro series: store and read back point in time.

A value published (first ALFRED vintage) on day V counts as known from the end of day V
UTC, so a daily bar can use it from the next day. Rows are only ever added: a revision is a
new vintage row, and a backtest asking "what was known before this bar" gets the figure
that was public then, never today's revised one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apex.core.errors import ProviderError
from apex.db.models import EconEvent, MacroObservation
from apex.news.fred import RELEASES, SERIES, SERIES_BY_ID, FredClient

REFETCH_DAYS = 400  # revisions mostly land within a year; re-read that much each update


def known_at(vintage: date) -> datetime:
    return datetime.combine(vintage, time(23, 59, 59), UTC)


def store_events(s: Session, source: str, release_id: str, name: str, category: str,
                 dates: list[date], now: datetime) -> int:
    have = set(s.scalars(select(EconEvent.event_date).where(
        EconEvent.source == source, EconEvent.release_id == release_id)).all())
    new = [d for d in dates if d not in have]
    s.add_all(EconEvent(source=source, release_id=release_id, name=name, category=category,
                        event_date=d, fetched_at=now) for d in new)
    s.flush()
    return len(new)


def store_observations(s: Session, series_id: str, rows: list[dict], now: datetime) -> int:
    have = set(s.execute(select(MacroObservation.obs_date, MacroObservation.vintage)
                         .where(MacroObservation.series_id == series_id)).all())
    new = [r for r in rows if (r["date"], r["vintage"]) not in have]
    s.add_all(MacroObservation(series_id=series_id, obs_date=r["date"], vintage=r["vintage"],
                               value=r["value"], known_at=known_at(r["vintage"]),
                               fetched_at=now) for r in new)
    s.flush()
    return len(new)


def fetch_macro(s: Session, fred: FredClient, *, series: list[str] | None = None,
                releases: bool = True, now: datetime | None = None) -> dict:
    """Update the calendar and the series. One failure never stops the rest."""
    now = now or datetime.now(UTC)
    out: dict = {"events": {}, "series": {}, "errors": []}
    if releases:
        try:
            ids = fred.release_ids()
        except ProviderError as exc:
            out["errors"].append(f"release list: {exc}")
            ids = {}
        for rel in RELEASES if ids else []:
            rid = ids.get(rel.name.lower())
            if rid is None:
                out["errors"].append(f"{rel.name}: not found in FRED's release list")
                continue
            try:
                out["events"][rel.label] = store_events(s, "fred", str(rid), rel.label,
                                                        rel.category, fred.release_dates(rid), now)
                s.commit()
            except ProviderError as exc:
                s.rollback()
                out["errors"].append(f"{rel.name}: {exc}")
    for sid in series or [x.id for x in SERIES]:
        last = s.scalar(select(func.max(MacroObservation.obs_date))
                        .where(MacroObservation.series_id == sid))
        start = None if last is None else last - timedelta(days=REFETCH_DAYS)
        try:
            out["series"][sid] = store_observations(s, sid, fred.observations(sid, start), now)
            s.commit()
        except ProviderError as exc:
            s.rollback()
            out["errors"].append(f"{sid}: {exc}")
    return out


def value_known_before(s: Session, series_id: str, at: datetime) -> tuple[date, float] | None:
    """The newest observation, as it was published, that was known strictly before ``at``."""
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware UTC")
    row = s.execute(select(MacroObservation.obs_date, MacroObservation.value)
                    .where(MacroObservation.series_id == series_id,
                           MacroObservation.known_at < at)
                    .order_by(MacroObservation.obs_date.desc(),
                              MacroObservation.vintage.desc()).limit(1)).first()
    return None if row is None else (row[0], row[1])


def latest_values(s: Session) -> list[dict]:
    """Each stored series' newest value (latest vintage) and the one before it."""
    out = []
    for ser in SERIES:
        rows = s.execute(select(MacroObservation.obs_date, MacroObservation.value)
                         .where(MacroObservation.series_id == ser.id)
                         .order_by(MacroObservation.obs_date.desc(),
                                   MacroObservation.vintage.desc()).limit(40)).all()
        seen: dict[date, float] = {}
        for d, v in rows:
            seen.setdefault(d, v)
        dates = sorted(seen, reverse=True)
        if not dates:
            continue
        prev = seen[dates[1]] if len(dates) > 1 else None
        out.append({"series_id": ser.id, "label": ser.label, "unit": ser.unit,
                    "date": dates[0].isoformat(), "value": seen[dates[0]],
                    "previous": prev,
                    "source_url": f"https://fred.stlouisfed.org/series/{ser.id}"})
    return out


def upcoming(s: Session, start: date, days: int = 14) -> list[EconEvent]:
    end = start + timedelta(days=days)
    q = select(EconEvent).where(EconEvent.event_date >= start, EconEvent.event_date < end)
    return list(s.scalars(q.order_by(EconEvent.event_date, EconEvent.name)).all())


def event_dates(s: Session, categories: list[str] | None = None) -> set[date]:
    """Release days (for "stand aside on big days" filters). Calendars are published ahead,
    so using a past date on that day is fair."""
    q = select(EconEvent.event_date)
    if categories:
        q = q.where(EconEvent.category.in_(categories))
    return set(s.scalars(q).all())


__all__ = ["SERIES_BY_ID", "event_dates", "fetch_macro", "latest_values", "upcoming",
           "value_known_before"]
