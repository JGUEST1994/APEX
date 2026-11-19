"""Research library: store legally obtained documents and search them.

This is the Phase 1 slice: plain-text ingestion, deduplication by content hash, chunking
and keyword search that works on every database. PDF/HTML extraction, concept extraction
and vector search (pgvector, migration 0002) come in Phase 2.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.db.models import ResearchChunk, ResearchDocument

_WORD = re.compile(r"[a-z0-9]+")


def chunk_text(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    text = re.sub(r"\s+", " ", text).strip()
    chunks, i = [], 0
    while i < len(text):
        chunks.append(text[i : i + size])
        if i + size >= len(text):
            break
        i += size - overlap
    return chunks


def add_document(session: Session, *, title: str, text: str, source_type: str,
                 source_ref: str = "", authors: str = "", published: str | None = None,
                 licence_note: str = "", subjects: list[str] | None = None) -> ResearchDocument:
    """Add a document; returns the existing row if identical content was already imported."""
    digest = hashlib.sha256(text.encode()).hexdigest()
    existing = session.scalar(select(ResearchDocument).where(ResearchDocument.sha256 == digest))
    if existing:
        return existing
    doc = ResearchDocument(title=title, authors=authors, published=published,
                           source_type=source_type, source_ref=source_ref,
                           licence_note=licence_note, sha256=digest, subjects=subjects or [])
    doc.chunks = [ResearchChunk(ordinal=i, text=c) for i, c in enumerate(chunk_text(text))]
    session.add(doc)
    session.flush()
    return doc


@dataclass
class SearchHit:
    document_id: int
    title: str
    chunk_ordinal: int
    score: float
    snippet: str


def keyword_search(session: Session, query: str, limit: int = 10) -> list[SearchHit]:
    terms = set(_WORD.findall(query.lower())) - {"the", "a", "an", "of", "and", "or", "to", "in"}
    if not terms:
        return []
    hits: list[SearchHit] = []
    rows = session.execute(select(ResearchChunk, ResearchDocument.title)
                           .join(ResearchDocument)).all()
    for chunk, title in rows:
        words = _WORD.findall(chunk.text.lower()) + _WORD.findall(title.lower())
        matched = terms & set(words)
        if not matched:
            continue
        score = len(matched) / len(terms) + sum(words.count(t) for t in matched) / (len(words) + 1)
        hits.append(SearchHit(chunk.document_id, title, chunk.ordinal, round(score, 4),
                              chunk.text[:240]))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]
