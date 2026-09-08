"""Knowledge base: ingests local documents into the vector store.

Bridges the ingestion pipeline to the knowledge_agent. Documents placed in
`knowledge/` (markdown/text/json/html — any format the extractors support)
are parsed with the full multi-strategy pipeline, chunked, and indexed.
Indexing is idempotent (mtime-keyed) and never raises: a bad document
produces diagnostics, not a failed agent run.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from .ingestion import RawDocument, get_store, ingest

_KNOWLEDGE_DIR = os.environ.get(
    "KNOWLEDGE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "knowledge"),
)
_mtimes: Dict[str, float] = {}


def _is_indexed(path: str) -> bool:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return True
    return _mtimes.get(path) == mtime


def ensure_indexed(directory: str | None = None) -> List[dict]:
    """Ingest new/changed documents. Returns per-file diagnostics."""
    root = directory or _KNOWLEDGE_DIR
    diagnostics: List[dict] = []
    if not os.path.isdir(root):
        return diagnostics
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isfile(path) or _is_indexed(path):
            continue
        ctype = {
            ".json": "application/json",
            ".html": "text/html",
            ".htm": "text/html",
        }.get(os.path.splitext(name)[1].lower(), "text/plain")
        try:
            with open(path, "rb") as f:
                raw = RawDocument(content=f.read(), content_type=ctype, url=f"file://{name}")
            chunks, _doc, diag = ingest(raw)
            if chunks:
                get_store().index_chunks(chunks)
            _mtimes[path] = os.path.getmtime(path)
        except Exception as exc:  # never break the agent on a bad document
            diag: Any = type("D", (), {"to_dict": lambda self, e=exc: {"error": str(e)}})()
        diagnostics.append({"file": name, **diag.to_dict()})
    return diagnostics
