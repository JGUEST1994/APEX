"""Research library: import legally obtained documents, extract knowledge, search.

Pipeline: file -> ``extract_file`` (text + metadata) -> ``extract_concepts`` (concepts,
formulas, rules, risk techniques; stored as untested hypotheses) -> chunks -> local
embeddings -> hybrid keyword + vector search. Works on SQLite and PostgreSQL with no
external AI service.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.db.models import ChunkVector, ResearchChunk, ResearchDocument
from apex.research.concepts import extract_concepts
from apex.research.embeddings import Embedder, default_embedder
from apex.research.extract import extract_file

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with", "is", "are",
         "all", "show", "me", "find", "what", "how", "about", "related", "covering", "by"}


def _terms(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


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
                 licence_note: str = "", subjects: list[str] | None = None,
                 embedder: Embedder | None = None, metadata: dict | None = None,
                 ) -> ResearchDocument:
    """Add a document; returns the existing row if identical content was already imported."""
    digest = hashlib.sha256(text.encode()).hexdigest()
    existing = session.scalar(select(ResearchDocument).where(ResearchDocument.sha256 == digest))
    if existing:
        return existing
    extracted = extract_concepts(text)
    if metadata:
        extracted["source_metadata"] = metadata
    doc = ResearchDocument(
        title=title[:512], authors=authors[:512], published=published, source_type=source_type,
        source_ref=source_ref, licence_note=licence_note, sha256=digest,
        subjects=subjects or list(extracted["concepts"])[:10], extracted=extracted)
    doc.chunks = [ResearchChunk(ordinal=i, text=c) for i, c in enumerate(chunk_text(text))]
    session.add(doc)
    session.flush()
    embed_chunks(session, doc.chunks, embedder or default_embedder())
    return doc


def import_file(session: Session, path: str | Path, *, licence_note: str,
                title: str | None = None, embedder: Embedder | None = None) -> ResearchDocument:
    if not licence_note.strip():
        raise ValueError("licence_note is required: record why APEX may lawfully use this file")
    ex = extract_file(path)
    if not ex.text.strip():
        raise ValueError(f"{path}: no extractable text (scanned PDF? OCR not supported yet)")
    return add_document(session, title=title or ex.title, text=ex.text,
                        source_type=ex.source_type, source_ref=str(Path(path).name),
                        authors=ex.authors, published=ex.published, licence_note=licence_note,
                        embedder=embedder, metadata=ex.metadata)


def embed_chunks(session: Session, chunks: list[ResearchChunk], embedder: Embedder) -> int:
    if not chunks:
        return 0
    vecs = embedder.embed([c.text for c in chunks]).astype(np.float32)
    for c, v in zip(chunks, vecs, strict=True):
        session.merge(ChunkVector(chunk_id=c.id, model=embedder.name, dim=embedder.dim,
                                  vector=v.tobytes()))
    session.flush()
    return len(chunks)


@dataclass
class SearchHit:
    document_id: int
    title: str
    chunk_ordinal: int
    score: float
    snippet: str
    keyword_score: float = 0.0
    vector_score: float = 0.0


def _bm25(query: list[str], docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> np.ndarray:
    n = len(docs)
    if not n or not query:
        return np.zeros(n)
    avgdl = sum(len(d) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in set(d))
    scores = np.zeros(n)
    for i, d in enumerate(docs):
        tf = Counter(d)
        for t in set(query):
            if tf[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                scores[i] += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avgdl))
    return scores


def search(session: Session, query: str, limit: int = 10, *, alpha: float = 0.5,
           embedder: Embedder | None = None) -> list[SearchHit]:
    """Hybrid search: ``alpha`` weights BM25 keyword relevance vs. vector similarity.

    Phase 2 scans chunks in memory: fine for tens of thousands of chunks. Larger libraries
    should move to PostgreSQL full-text + pgvector (migration 0002).
    """
    q_terms = _terms(query)
    if not q_terms:
        return []
    emb = embedder or default_embedder()
    rows = session.execute(select(ResearchChunk, ResearchDocument.title).join(ResearchDocument)
                           .order_by(ResearchChunk.id)).all()
    if not rows:
        return []
    chunk_ids = [c.id for c, _ in rows]
    kw = _bm25(q_terms, [_terms(c.text) + _terms(t) for c, t in rows])

    vec_rows = dict(session.execute(
        select(ChunkVector.chunk_id, ChunkVector.vector)
        .where(ChunkVector.model == emb.name, ChunkVector.chunk_id.in_(chunk_ids))).all())
    q = emb.embed([query])[0]
    vs = np.array([float(np.frombuffer(vec_rows[i], dtype=np.float32) @ q) if i in vec_rows
                   else 0.0 for i in chunk_ids])
    kw_n = kw / kw.max() if kw.max() > 0 else kw
    vs_n = np.clip(vs, 0, None)
    vs_n = vs_n / vs_n.max() if vs_n.max() > 0 else vs_n
    score = alpha * kw_n + (1 - alpha) * vs_n
    order = [i for i in np.argsort(-score, kind="stable") if kw[i] > 0 or vs[i] > 0.15]
    return [SearchHit(rows[i][0].document_id, rows[i][1], rows[i][0].ordinal,
                      round(float(score[i]), 4), rows[i][0].text[:240],
                      round(float(kw_n[i]), 4), round(float(vs[i]), 4)) for i in order[:limit]]


def keyword_search(session: Session, query: str, limit: int = 10) -> list[SearchHit]:
    """Keyword-only search (alpha=1). Kept for API compatibility."""
    return [h for h in search(session, query, limit, alpha=1.0) if h.keyword_score > 0]


def find_by_concept(session: Session, concept: str) -> list[ResearchDocument]:
    docs = session.scalars(select(ResearchDocument).order_by(ResearchDocument.id)).all()
    return [d for d in docs if concept in (d.extracted or {}).get("concepts", {})]


def formulas_for(session: Session, concept: str) -> list[tuple[str, str]]:
    """(document title, formula) for every document mentioning ``concept``."""
    return [(d.title, f) for d in find_by_concept(session, concept)
            for f in d.extracted.get("formulas", [])]
