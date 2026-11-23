"""Read RSS 2.0 and Atom feeds into plain records (no network here).

Python's XML parser does not fetch external entities, and feeds over 5 MB are refused before
parsing, so a hostile feed cannot make APEX read local files or exhaust memory.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

MAX_BYTES = 5_000_000
ATOM = "{http://www.w3.org/2005/Atom}"
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


@dataclass
class Entry:
    source_id: str
    title: str
    published_at: datetime
    url: str | None = None
    summary: str | None = None
    symbols: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


class FeedError(ValueError):
    pass


def clean_text(s: str | None, limit: int = 2000) -> str | None:
    """Plain text: HTML tags and entities removed, whitespace squeezed, length capped."""
    if not s:
        return None
    out = _SPACE.sub(" ", html.unescape(_TAGS.sub(" ", s))).strip()
    return out[:limit] or None


def safe_url(u: str | None) -> str | None:
    """Only http(s) links are kept, so a feed cannot plant a script link on the dashboard."""
    u = (u or "").strip()
    return u if u.lower().startswith(("https://", "http://")) else None


def parse_time(s: str | None) -> datetime | None:
    """RFC 822 (RSS) or ISO 8601 (Atom) to UTC; a time with no zone is read as UTC."""
    if not s:
        return None
    s = s.strip()
    try:
        t = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            t = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return (t.replace(tzinfo=UTC) if t.tzinfo is None else t).astimezone(UTC)


def _text(node: ET.Element | None, tag: str) -> str | None:
    if node is None:
        return None
    child = node.find(tag)
    return child.text if child is not None and child.text else None


def parse_feed(data: bytes) -> list[Entry]:
    """Entries from an RSS or Atom document. Entries with no usable time are skipped,
    because an item that can't be placed in time can't be used fairly."""
    if len(data) > MAX_BYTES:
        raise FeedError(f"feed is larger than {MAX_BYTES} bytes")
    if b"<!ENTITY" in data[:4096]:
        raise FeedError("feed declares XML entities; refused")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FeedError(f"not a readable feed: {exc}") from None
    out: list[Entry] = []
    if root.tag == f"{ATOM}feed":
        for e in root.findall(f"{ATOM}entry"):
            when = parse_time(_text(e, f"{ATOM}published") or _text(e, f"{ATOM}updated"))
            title = clean_text(_text(e, f"{ATOM}title"), 500)
            link = next((ln.get("href") for ln in e.findall(f"{ATOM}link")
                         if ln.get("rel", "alternate") == "alternate"), None)
            sid = _text(e, f"{ATOM}id") or link
            if when and title and sid:
                out.append(Entry(sid.strip(), title, when, safe_url(link),
                                 clean_text(_text(e, f"{ATOM}summary")
                                            or _text(e, f"{ATOM}content"))))
        return out
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FeedError(f"unknown feed format <{root.tag}>")
    for it in channel.findall("item"):
        when = parse_time(_text(it, "pubDate")
                          or _text(it, "{http://purl.org/dc/elements/1.1/}date"))
        title = clean_text(_text(it, "title"), 500)
        link = _text(it, "link")
        sid = _text(it, "guid") or link
        if when and title and sid:
            out.append(Entry(sid.strip(), title, when, safe_url(link),
                             clean_text(_text(it, "description"))))
    return out
