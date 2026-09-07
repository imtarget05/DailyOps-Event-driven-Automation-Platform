"""Ingestion pipeline — public surface.

    from src.rag.ingestion import ingest, index, get_store

    chunks, doc, diag = ingest(raw_bytes_or_url, url=..., content_type=...)
    index(chunks)                     # push to the vector store
    diag.to_dict()                    # stage-by-stage diagnostics
"""
from .extractors import (  # noqa: F401
    EXTRACTOR_REGISTRY,
    JsonExtractor,
    PlainTextFallbackExtractor,
    SemanticHtmlExtractor,
    StructuredMetadataExtractor,
    extract_all,
    register_extractor,
)
from .models import (  # noqa: F401
    CanonicalDocument,
    Chunk,
    ContentBlock,
    IngestionDiagnostics,
    RawDocument,
)
from .pipeline import chunk, deduplicate, fetch, ingest, normalize, validate  # noqa: F401
from .store import get_store  # noqa: F401


def index(chunks_or_doc):
    """Index chunks (or a CanonicalDocument) into the default vector store."""
    store = get_store()
    if isinstance(chunks_or_doc, CanonicalDocument):
        return store.index_chunks(chunk(chunks_or_doc))
    return store.index_chunks(chunks_or_doc)
