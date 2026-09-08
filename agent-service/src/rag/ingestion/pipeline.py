"""Ingestion pipeline orchestration.

    Fetcher -> RawDocument
            -> Extractor(s)          (multi-strategy, registry-based)
            -> CanonicalDocument     (normalizer -> deduplicator)
            -> Quality Validator
            -> Chunker
            -> VectorStore (see store.py)

Every stage records diagnostics so information loss can be localized to the
exact stage. No stage is domain-, vendor-, or field-specific.
"""
from __future__ import annotations

import re
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from .extractors import extract_all
from .models import (
    KIND_METADATA,
    KIND_STRUCTURED,
    CanonicalDocument,
    Chunk,
    ContentBlock,
    IngestionDiagnostics,
    RawDocument,
)

# ---------------------------------------------------------------------------
# Fetcher (pluggable renderer extension point)


def fetch(url: str, timeout: float = 15.0,
          renderer: Optional[Callable[[str], RawDocument]] = None,
          max_bytes: int = 5_000_000) -> RawDocument:
    """Fetch a URL into a RawDocument.

    `renderer` is the extension point for future browser rendering: when
    provided, it fully owns retrieval (e.g. headless browser) and returns a
    RawDocument of the *rendered* content. When absent (current default),
    only the static HTTP response is available — client-rendered content is
    flagged by the extraction stage, never silently dropped or faked.
    """
    if renderer is not None:
        return renderer(url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DailyOps-ingest/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read(max_bytes)
            return RawDocument(content=content,
                               content_type=resp.headers.get("Content-Type", ""),
                               url=url, status=resp.status,
                               headers={k.lower(): v for k, v in resp.headers.items()})
    except Exception as exc:
        return RawDocument(content=b"", url=url, status=0, fetch_error=str(exc))


# ---------------------------------------------------------------------------
# Normalizer: unify whitespace/casing of block text, detect metadata conflicts


def normalize(doc: CanonicalDocument) -> CanonicalDocument:
    seen_fields: Dict[str, Any] = {}
    conflicts: List[str] = []
    malformed_structured = 0
    for block in doc.blocks:
        text = " ".join(block.text.split())
        if block.meta.get("jsonld_error") == "malformed":
            malformed_structured += 1
        if block.kind == KIND_METADATA and ":" in text:
            field, _, value = text.partition(":")
            field = field.strip().lower()
            value = value.strip()
            block.text = f"{field}: {value}"
            block.meta["field"] = field
            if field in seen_fields and seen_fields[field] != value:
                conflicts.append(field)
            seen_fields.setdefault(field, value)
        block.text = text
    doc.metadata["_conflicting_fields"] = sorted(set(conflicts))
    doc.metadata["_malformed_structured"] = malformed_structured
    return doc


# ---------------------------------------------------------------------------
# Deduplicator: exact + near-duplicate removal across strategies

_TOKEN_RE = re.compile(
    r"[a-z0-9àáạảãăằắẳẵặâầấẩẫậèéẹẻẽêềếểễệìíịỉĩòóọỏõôồốổỗộơờớởỡợùúụủũưừứửữựỳýỵỷỹđ]+")


def _tokens(text: str) -> frozenset:
    return frozenset(_TOKEN_RE.findall(text.lower()))


def deduplicate(doc: CanonicalDocument, jaccard_threshold: float = 0.8) -> CanonicalDocument:
    kept: List[ContentBlock] = []
    seen_exact: set = set()
    seen_tokens: List[frozenset] = []
    for block in doc.blocks:
        if not block.text.strip():
            continue
        key = block.text.lower()
        if key in seen_exact:
            continue
        toks = _tokens(block.text)
        near = any(len(toks & prev) / max(1, len(toks | prev)) >= jaccard_threshold
                   for prev in seen_tokens)
        if near:
            continue
        seen_exact.add(key)
        seen_tokens.append(toks)
        kept.append(block)
    doc.blocks = kept
    return doc


# ---------------------------------------------------------------------------
# Quality validator


def validate(doc: CanonicalDocument,
             min_chars: int = 20,
             min_quality_ratio: float = 0.05) -> List[str]:
    """Returns a list of quality warnings. Empty list == healthy ingestion."""
    warnings: List[str] = []
    if not doc.blocks:
        warnings.append("quality: no content extracted")
        return warnings
    text = doc.text
    if len(text) < min_chars:
        warnings.append(f"quality: extracted content too short ({len(text)} chars)")
    if doc.raw_char_count > 500 and doc.quality_ratio() < min_quality_ratio:
        warnings.append(
            f"quality: severe degradation vs raw source ({doc.quality_ratio():.3f} ratio)")
    for field in doc.metadata.get("_conflicting_fields", []):
        warnings.append(f"quality: conflicting values for metadata field '{field}'")
    if doc.metadata.get("_malformed_structured"):
        warnings.append("quality: malformed embedded structured data skipped")
    if doc.report and not doc.report.succeeded:
        warnings.append("quality: all structural extractors failed")
    return warnings


# ---------------------------------------------------------------------------
# Chunker


def chunk(doc: CanonicalDocument, max_chars: int = 800, overlap: int = 100) -> List[Chunk]:
    """Character-bounded chunks that never split a block mid-way and carry
    provenance metadata into the vector store."""
    chunks: List[Chunk] = []
    buf: List[str] = []
    buf_len = 0
    kinds: set = set()

    def emit() -> None:
        nonlocal buf, buf_len, kinds
        text = "\n".join(buf).strip()
        if text:
            chunks.append(Chunk(text=text, source_url=doc.source_url,
                                index=len(chunks),
                                kind=KIND_STRUCTURED if KIND_STRUCTURED in kinds else "text",
                                meta={"kinds": sorted(kinds)}))
        buf, buf_len, kinds = [], 0, set()

    for block in doc.blocks:
        text = block.text
        if len(text) > max_chars:
            emit()
            step = max(1, max_chars - overlap)
            for i in range(0, len(text), step):
                part = text[i:i + max_chars]
                chunks.append(Chunk(text=part, source_url=doc.source_url,
                                    index=len(chunks), kind=block.kind,
                                    meta={"extractor": block.extractor}))
            continue
        if buf_len + len(text) > max_chars and buf:
            emit()
        buf.append(text)
        buf_len += len(text)
        kinds.add(block.kind)
    emit()
    return chunks


# ---------------------------------------------------------------------------
# Full pipeline


def ingest(source: Any, url: str = "", content_type: str = "") -> tuple:
    """Run the whole ingestion pipeline for one source.

    `source` may be a URL string, raw bytes, or a pre-built RawDocument.
    Returns (chunks, CanonicalDocument, IngestionDiagnostics).
    Never raises for fetch/extraction failures — diagnostics carry the error.
    """
    if isinstance(source, RawDocument):
        raw = source
    elif isinstance(source, (bytes, bytearray)):
        raw = RawDocument(content=bytes(source), content_type=content_type, url=url)
    elif isinstance(source, str) and source.startswith(("http://", "https://")):
        raw = fetch(source)
    else:
        raw = RawDocument(content=source.encode("utf-8"), content_type=content_type,
                          url=url)

    diag = IngestionDiagnostics(source_url=raw.url or url)
    diag.fetched = raw.fetch_error is None
    diag.fetch_error = raw.fetch_error

    blocks, report = extract_all(raw)
    doc = CanonicalDocument(source_url=raw.url or url, blocks=list(blocks),
                            report=report, raw_char_count=len(raw.text))
    diag.extracted_chars = len(doc.text)

    normalize(doc)
    diag.normalized_chars = len(doc.text)

    deduplicate(doc)
    diag.deduplicated_chars = len(doc.text)

    warnings = validate(doc)
    diag.warnings = warnings
    diag.valid = not any(w.startswith("quality: no content") for w in warnings)
    diag.limitations = list(report.limitations)

    chunks = chunk(doc)
    diag.chunk_count = len(chunks)
    diag.report = report
    return chunks, doc, diag
