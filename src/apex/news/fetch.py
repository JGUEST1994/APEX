"""Fetch a set of feeds once and store what's new (used by the CLI and the scheduler)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from apex.core.errors import ProviderError
from apex.news.sources import FEEDS, Fetcher
from apex.news.store import store_entries


def fetcher_for(settings, client=None, sleep=None) -> Fetcher:  # noqa: ANN001
    keys = None
    if settings.alpaca_key_id and settings.alpaca_secret_key:
        keys = (settings.alpaca_key_id.get_secret_value(),
                settings.alpaca_secret_key.get_secret_value())
    kw = {"sleep": sleep} if sleep else {}
    return Fetcher(client, sec_user_agent=settings.sec_user_agent, alpaca_keys=keys, **kw)


def fetch_feeds(s: Session, fetcher: Fetcher, names: list[str] | None = None,
                now: datetime | None = None) -> dict:
    """One pass over the named feeds (all by default). A failing feed is reported in
    ``errors`` and never stops the others."""
    now = now or datetime.now(UTC)
    names = names or list(FEEDS)
    stored, errors = {}, []
    for name in names:
        feed = FEEDS.get(name)
        if feed is None:
            errors.append(f"{name}: unknown feed (choose from {', '.join(FEEDS)})")
            continue
        try:
            entries = fetcher.entries(feed)
            stored[name] = store_entries(s, feed, entries, now)
            s.commit()
        except ProviderError as exc:
            s.rollback()
            errors.append(f"{name}: {exc}")
    return {"stored": stored, "errors": errors}
