"""Minimal in-memory vector store (stdlib only).

Scoring combines the pluggable embedder (src.rag.embeddings.embed) with a
lexical overlap term so retrieval already behaves sensibly with the stub
embedder; swapping in real embeddings requires no change here.

Ingested documents are registered via `index_chunks()`; the retriever reads
from this store as an additional evidence source (backward compatible).
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Tuple

from .models import Chunk


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._chunks: List[Chunk] = []
        self._vectors: List[List[float]] = []
        self._tokens: List[Counter] = []

    # -- indexing -----------------------------------------------------------
    def index_chunks(self, chunks: List[Chunk]) -> int:
        from ..embeddings import embed
        added = 0
        for chunk in chunks:
            self._chunks.append(chunk)
            self._vectors.append(embed(chunk.text))
            self._tokens.append(Counter(_tokenize(chunk.text)))
            added += 1
        return added

    def index_document(self, doc) -> int:
        """Index a CanonicalDocument (chunking is done here if not yet done)."""
        from .pipeline import chunk as chunk_doc
        chunks = chunk_doc(doc) if not isinstance(doc, list) else doc
        return self.index_chunks(chunks)

    def __len__(self) -> int:
        return len(self._chunks)

    def clear(self) -> None:
        self._chunks, self._vectors, self._tokens = [], [], []

    # -- retrieval ------------------------------------------------------------
    def search(self, query: str, top_k: int = 3) -> List[Tuple[Chunk, float]]:
        if not self._chunks:
            return []
        from ..embeddings import embed

        q_vec = embed(query)
        q_tokens = Counter(_tokenize(query))
        scored: List[Tuple[float, int]] = []
        for i, (vec, toks) in enumerate(zip(self._vectors, self._tokens)):
            cos = _cosine(q_vec, vec)
            overlap = sum((q_tokens & toks).values()) / max(1, sum(q_tokens.values()))
            scored.append((0.5 * cos + 0.5 * overlap, i))
        scored.sort(reverse=True)
        return [(self._chunks[i], score) for score, i in scored[:top_k] if score > 0]


def _tokenize(text: str) -> List[str]:
    import re
    return re.findall(r"[a-z0-9à-ỹ]{2,}", text.lower())


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# Module-level default store (swap for a real vector DB behind this seam later)
_default_store = InMemoryVectorStore()


def get_store() -> InMemoryVectorStore:
    return _default_store
