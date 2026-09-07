"""Canonical document models for the ingestion pipeline.

Pipeline contract:
    Fetcher -> RawDocument -> Extractor(s) -> CanonicalDocument
            -> Normalizer -> Deduplicator -> QualityValidator -> Chunker

A CanonicalDocument is the single representation the downstream pipeline
(chunker/embedder/vector store) understands. It must never depend on a
specific source, domain, or format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Content block kinds — generic, not domain-specific.
KIND_TEXT = "text"
KIND_HEADING = "heading"
KIND_LIST = "list"
KIND_TABLE = "table"
KIND_METADATA = "metadata"       # page-level metadata (title, description, ...)
KIND_STRUCTURED = "structured"   # embedded structured data (e.g. JSON-LD flattened)

VALID_KINDS = {KIND_TEXT, KIND_HEADING, KIND_LIST, KIND_TABLE, KIND_METADATA, KIND_STRUCTURED}


@dataclass
class ContentBlock:
    """One atomic piece of extracted information with provenance."""
    text: str
    kind: str = KIND_TEXT
    extractor: str = "unknown"          # which strategy produced it
    weight: float = 1.0                 # relative retrieval weight
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            self.kind = KIND_TEXT


@dataclass
class RawDocument:
    """Untouched bytes of a fetched source plus response context."""
    content: bytes
    content_type: str = "text/html"
    url: str = ""
    status: int = 200
    headers: Dict[str, str] = field(default_factory=dict)
    fetch_error: Optional[str] = None

    @property
    def text(self) -> str:
        """Best-effort decode without assuming an encoding."""
        if not self.content:
            return ""
        charset = "utf-8"
        for part in (self.content_type, self.headers.get("content-type", "")):
            for token in part.split(";"):
                token = token.strip().lower()
                if token.startswith("charset="):
                    charset = token.split("=", 1)[1].strip("'\" ") or charset
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:  # unknown charset declared
            return self.content.decode("utf-8", errors="replace")


@dataclass
class ExtractionReport:
    """Per-extractor diagnostics: what ran, what succeeded, what failed."""
    ran: List[str] = field(default_factory=list)
    succeeded: List[str] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)   # extractor -> error
    warnings: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)   # known capability limits
    dynamic_content_suspected: bool = False


@dataclass
class CanonicalDocument:
    """Normalized, extractor-agnostic document representation."""
    source_url: str
    blocks: List[ContentBlock] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    report: ExtractionReport = field(default_factory=ExtractionReport)
    raw_char_count: int = 0

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text.strip())

    def quality_ratio(self) -> float:
        """Extracted vs raw size — a coarse degradation signal (never None)."""
        if self.raw_char_count <= 0:
            return 1.0
        return min(1.0, len(self.text) / self.raw_char_count)


@dataclass
class Chunk:
    """Chunk payload pushed to the vector store."""
    text: str
    source_url: str
    index: int
    kind: str = KIND_TEXT
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestionDiagnostics:
    """Stage-by-stage trace: source -> fetched -> extracted -> normalized ->
    deduplicated -> validated -> chunked. Information loss is localized here."""
    source_url: str
    fetched: bool = False
    fetch_error: Optional[str] = None
    extracted_chars: int = 0
    normalized_chars: int = 0
    deduplicated_chars: int = 0
    chunk_count: int = 0
    valid: bool = True
    warnings: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    report: Optional[ExtractionReport] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_url": self.source_url,
            "fetched": self.fetched,
            "fetch_error": self.fetch_error,
            "chars": {
                "extracted": self.extracted_chars,
                "normalized": self.normalized_chars,
                "deduplicated": self.deduplicated_chars,
            },
            "chunk_count": self.chunk_count,
            "valid": self.valid,
            "warnings": self.warnings,
            "limitations": self.limitations,
            "extraction": {
                "ran": self.report.ran if self.report else [],
                "succeeded": self.report.succeeded if self.report else [],
                "failed": self.report.failed if self.report else {},
                "dynamic_content_suspected": (
                    self.report.dynamic_content_suspected if self.report else False
                ),
            },
        }
