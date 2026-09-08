"""Embeddings stub — swap with sentence-transformers/Cloudflare embeddings later."""
from __future__ import annotations


def embed(text: str) -> list[float]:
    return [float(len(text) % 97) / 97.0]
