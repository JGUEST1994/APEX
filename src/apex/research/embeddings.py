"""Local text embeddings for semantic search. No external API is ever required.

``HashingEmbedder`` (default) is dependency-free and deterministic: signed feature hashing
of word unigrams and bigrams, L2-normalised. It captures lexical similarity well and is
the guaranteed fallback. ``SentenceTransformerEmbedder`` gives real semantic similarity
when the optional ``sentence-transformers`` package and a local model are installed.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np

_WORD = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.name = f"hashing-v1-{dim}"

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        words = _WORD.findall(text.lower())
        feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        v = np.sign(v) * np.log1p(np.abs(v))
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        return np.vstack([self._vec(t) for t in texts])


class SentenceTransformerEmbedder:  # pragma: no cover - optional dependency
    def __init__(self, model: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self._m = SentenceTransformer(model)
        self.dim = int(self._m.get_sentence_embedding_dimension())
        self.name = f"st-{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self._m.encode(texts, normalize_embeddings=True), dtype=np.float32)


def default_embedder() -> Embedder:
    return HashingEmbedder()
