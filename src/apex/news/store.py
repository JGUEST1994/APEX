"""Store feed entries point in time, and read them back without look-ahead.

``known_at = max(published_at, first_seen_at)``. A backtest bar may use an item only if the
bar opens strictly after ``known_at`` (bar ``ts`` is the open time), so news can never move a
trade on the bar it arrived in or earlier. Changed items become a new version row; stored
rows are never edited.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from apex.db.models import NewsItem, NewsSymbol
from apex.news.parse import Entry
from apex.news.sources import Feed

DELAYED_AFTER = timedelta(minutes=15)
HISTORICAL_AFTER = timedelta(days=1)


def content_hash(e: Entry) -> str:
    return hashlib.sha256(f"{e.title}\n{e.summary or ''}\n{e.url or ''}".encode()).hexdigest()


def store_entries(s: Session, feed: Feed, entries: Iterable[Entry], now: datetime) -> dict:
    """Add new items and new versions of changed items. Returns counts."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    added = changed = same = 0
    for e in entries:
        h = content_hash(e)
        latest = s.scalars(select(NewsItem).where(NewsItem.source == feed.name,
                                                  NewsItem.source_id == e.source_id[:255])
                           .order_by(NewsItem.version.desc()).limit(1)).first()
        if latest is not None and latest.content_hash == h:
            same += 1
            continue
        item = NewsItem(source=feed.name, source_id=e.source_id[:255],
                        version=1 if latest is None else latest.version + 1, kind=feed.kind,
                        title=e.title, summary=e.summary, url=e.url,
                        published_at=e.published_at, first_seen_at=now,
                        known_at=max(e.published_at, now), content_hash=h,
                        licence=feed.licence[:255], extra=e.extra or {})
        item.symbols = [NewsSymbol(symbol=sym[:32],
                                   linked_by="cik" if feed.fmt == "sec" else "source")
                        for sym in dict.fromkeys(e.symbols)]
        s.add(item)
        s.flush()
        added += latest is None
        changed += latest is not None
    return {"new": added, "changed": changed, "unchanged": same}


def label(item: NewsItem) -> str:
    """live, delayed or historical: how long after publication APEX first saw it."""
    lag = item.first_seen_at - item.published_at
    if lag > HISTORICAL_AFTER:
        return "historical"
    return "delayed" if lag > DELAYED_AFTER else "live"


def latest(s: Session, *, limit: int = 50, symbol: str | None = None,
           source: str | None = None, kind: str | None = None) -> list[NewsItem]:
    """Newest first, latest version of each item only."""
    newest = (select(NewsItem.source, NewsItem.source_id,
                     func.max(NewsItem.version).label("v"))
              .group_by(NewsItem.source, NewsItem.source_id).subquery())
    q = (select(NewsItem).join(newest, (NewsItem.source == newest.c.source)
                               & (NewsItem.source_id == newest.c.source_id)
                               & (NewsItem.version == newest.c.v))
         .options(selectinload(NewsItem.symbols)))
    if symbol:
        q = q.where(NewsItem.id.in_(select(NewsSymbol.item_id)
                                    .where(NewsSymbol.symbol == symbol.upper())))
    if source:
        q = q.where(NewsItem.source == source)
    if kind:
        q = q.where(NewsItem.kind == kind)
    return list(s.scalars(q.order_by(NewsItem.published_at.desc(), NewsItem.id.desc())
                          .limit(limit)).all())


def known_before_bar(s: Session, bar_open: datetime, *, symbol: str | None = None,
                     since: datetime | None = None) -> list[NewsItem]:
    """Item versions a strategy may use on the bar that opens at ``bar_open``: only those
    with ``known_at`` strictly before it. This is the one door backtests use."""
    if bar_open.tzinfo is None:
        raise ValueError("bar_open must be timezone-aware UTC")
    q = select(NewsItem).where(NewsItem.known_at < bar_open.astimezone(UTC))
    if since is not None:
        q = q.where(NewsItem.known_at >= since)
    if symbol:
        q = q.where(NewsItem.id.in_(select(NewsSymbol.item_id)
                                    .where(NewsSymbol.symbol == symbol.upper())))
    return list(s.scalars(q.order_by(NewsItem.known_at, NewsItem.id)).all())
