"""Ingestion pipeline tests — organized by FAILURE CATEGORY, not by bug instance.

Contract under test (behavior, not implementation):
- Source preservation: information present in the raw response reaches the
  canonical document / chunks unless explicitly flagged.
- Multi-strategy extraction with fallback: a failing strategy degrades
  gracefully instead of losing the document.
- Deduplication & normalization happen before quality validation & chunking.
- Diagnostics localize information loss to a pipeline stage.
- No extractor depends on a specific domain/field/vendor.
All fixtures are offline/synthetic for determinism.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.rag.ingestion import (
    CanonicalDocument,
    RawDocument,
    deduplicate,
    ingest,
    validate,
)
from src.rag.ingestion.extractors import EXTRACTOR_REGISTRY, register_extractor
from src.rag.ingestion.models import ContentBlock
from src.rag.ingestion.store import get_store


def _html(body: str, meta: str = "", title: str = "Doc") -> bytes:
    return f"""<!DOCTYPE html><html><head><title>{title}</title>{meta}</head>
<body>{body}</body></html>""".encode("utf-8")


# ---------------------------------------------------------------- standard

def test_standard_html_document_preserves_visible_text():
    raw = RawDocument(content=_html("<p>Restock threshold is 500 units.</p>"
                                    "<p>Refunds above 100 need approval.</p>"),
                      content_type="text/html", url="https://example.test/sop")
    chunks, doc, diag = ingest(raw)
    text = "\n".join(c.text for c in chunks)
    assert "Restock threshold is 500 units." in text
    assert "Refunds above 100 need approval." in text
    assert diag.valid and not diag.warnings
    assert "<script" not in text


def test_script_and_style_are_noise_not_content():
    raw = RawDocument(content=_html(
        "<style>.a{color:red}</style><p>Pricing approval required.</p>"
        "<script>var tracking={pixel:1};</script>"),
        content_type="text/html", url="https://example.test/p")
    chunks, doc, diag = ingest(raw)
    text = "\n".join(c.text for c in chunks)
    assert "Pricing approval required." in text
    assert "tracking" not in text and "color:red" not in text


# ---------------------------------------------------------------- structured

def test_json_ld_structured_data_is_preserved():
    ld = ('<script type="application/ld+json">'
          '{"@type":"Product","name":"Widget","offers":{"price":"19.99"}}</script>')
    raw = RawDocument(content=_html("<p>Buy the widget.</p>" + ld),
                      content_type="text/html", url="https://example.test/p")
    chunks, doc, diag = ingest(raw)
    text = "\n".join(c.text for c in chunks)
    assert "19.99" in text and "Widget" in text
    assert "structured_metadata" in doc.report.succeeded


def test_json_api_payload_is_fully_flattened():
    raw = RawDocument(content=b'{"order":{"id":"A1","total":42.5},"items":["x","y"]}',
                      content_type="application/json", url="https://api.test/order")
    chunks, doc, diag = ingest(raw)
    text = "\n".join(c.text for c in chunks)
    assert "order.id: A1" in text and "42.5" in text
    assert diag.valid


# ---------------------------------------------------------------- metadata-heavy

def test_metadata_heavy_document_exposes_metadata():
    meta = ('<meta name="description" content="Quarterly SOP for restocking">'
            '<meta property="og:title" content="SOP v3">')
    raw = RawDocument(content=_html("<p>Body text here.</p>", meta=meta),
                      content_type="text/html", url="https://example.test/sop")
    chunks, doc, diag = ingest(raw)
    text = "\n".join(c.text for c in chunks)
    assert "description: Quarterly SOP for restocking" in text
    assert "SOP v3" in text


# ---------------------------------------------------------------- malformed

def test_partially_malformed_html_still_extracts_readable_parts():
    raw = RawDocument(content=b"<html><head><title>Broken</title><p>Unclosed tag text"
                              b"<div><p>Second paragraph</div></body></html>",
                      content_type="text/html", url="https://example.test/broken")
    chunks, doc, diag = ingest(raw)
    assert "Second paragraph" in "\n".join(c.text for c in chunks)


def test_malformed_json_ld_is_skipped_not_fatal():
    ld = '<script type="application/ld+json">{"broken": tru</script>'
    raw = RawDocument(content=_html("<p>Visible content survives.</p>" + ld),
                      content_type="text/html", url="https://example.test/m")
    chunks, doc, diag = ingest(raw)
    assert "Visible content survives." in "\n".join(c.text for c in chunks)
    assert diag.valid


def test_malformed_jsonld_produces_quality_warning():
    ld = '<script type="application/ld+json">{"broken": tru</script>'
    raw = RawDocument(content=_html("<p>x</p>" + ld), content_type="text/html")
    _, doc, diag = ingest(raw)
    assert any("malformed" in w for w in diag.warnings)


# ---------------------------------------------------------------- duplicated

def test_duplicated_information_is_removed_before_chunking():
    raw = RawDocument(content=_html("<p>Free shipping applies to orders over 50.</p>" * 4),
                      content_type="text/html", url="https://example.test/ship")
    _, doc, diag = ingest(raw)
    text_blocks = [b for b in doc.blocks if b.kind != "metadata"]
    assert len(text_blocks) == 1  # exact + near duplicates collapsed
    assert diag.deduplicated_chars < diag.extracted_chars


def test_deduplicate_keeps_genuinely_different_blocks():
    doc = CanonicalDocument(source_url="u", blocks=[
        ContentBlock(text="Policy A: restock at 500"),
        ContentBlock(text="Policy B: refund needs approval")])
    deduplicate(doc)
    assert len(doc.blocks) == 2



# ---------------------------------------------------------------- conflicting

def test_conflicting_metadata_is_kept_and_flagged():
    meta = ('<meta name="author" content="Alice">'
            '<meta name="author" content="Bob">'
            '<meta name="description" content="x">')
    raw = RawDocument(content=_html("<p>Body.</p>", meta=meta),
                      content_type="text/html", url="https://example.test/c")
    chunks, doc, diag = ingest(raw)
    text = "\n".join(c.text for c in chunks)
    assert "author: Alice" in text and "author: Bob" in text  # preserved, not dropped
    assert any("conflicting" in w and "author" in w for w in diag.warnings)


# ---------------------------------------------------------------- missing

def test_empty_response_yields_invalid_with_diagnostics():
    raw = RawDocument(content=b"", content_type="text/html", url="https://x.test/")
    chunks, doc, diag = ingest(raw)
    assert chunks == [] and not diag.valid
    assert any("no content" in w for w in diag.warnings)


def test_document_without_any_metadata_still_ingests():
    raw = RawDocument(content=_html("<p>Plain body only.</p>"),
                      content_type="text/html", url="https://example.test/p")
    chunks, doc, diag = ingest(raw)
    assert "Plain body only." in "\n".join(c.text for c in chunks)
    assert diag.valid


def test_fetch_failure_is_reported_not_raised():
    raw = RawDocument(content=b"", url="https://unreachable.test/", status=0,
                      fetch_error="connection refused")
    chunks, doc, diag = ingest(raw)
    assert not diag.fetched and diag.fetch_error == "connection refused"
    assert chunks == []



# ---------------------------------------------------------------- dynamic content

def test_client_rendered_page_is_flagged_not_faked():
    script = "<script>" + "var x=1;" * 800 + "</script>"
    raw = RawDocument(content=_html("<div id=root></div>" + script),
                      content_type="text/html", url="https://spa.test/app")
    chunks, doc, diag = ingest(raw)
    assert doc.report.dynamic_content_suspected is True
    assert any("dynamic_content" in lim for lim in diag.limitations)
    joined = "\n".join(c.text for c in chunks)
    assert "var x=1" not in joined  # safe behavior: nothing fake is injected


def test_noscript_hint_flags_limitation():
    raw = RawDocument(content=_html('<noscript>Enable JS</noscript><p>Hello.</p>'),
                      content_type="text/html", url="https://x.test/")
    _, doc, diag = ingest(raw)
    assert any("dynamic_content" in lim for lim in diag.limitations)


def test_renderer_extension_point_is_honored():
    calls = []

    def renderer(url):
        calls.append(url)
        return RawDocument(content=_html("<p>Rendered by browser.</p>"),
                           content_type="text/html", url=url)

    from src.rag.ingestion import fetch
    raw = fetch("https://spa.test/app", renderer=renderer)
    assert calls == ["https://spa.test/app"]
    chunks, _, _ = ingest(raw)
    assert "Rendered by browser." in "\n".join(c.text for c in chunks)


# ---------------------------------------------------------------- fallback

def test_unknown_format_falls_back_gracefully():
    raw = RawDocument(content=b"plain text report line 1\nplain text report line 2\n",
                      content_type="text/plain", url="https://x.test/r.txt")
    chunks, doc, diag = ingest(raw)
    assert "plain text report line 1" in "\n".join(c.text for c in chunks)
    assert "plain_text_fallback" in doc.report.succeeded
    assert diag.valid


def test_failing_extractor_does_not_kill_pipeline():
    class Boom:
        name = "boom"

        def extract(self, raw):
            raise RuntimeError("boom")

    position = len(EXTRACTOR_REGISTRY) - 1
    register_extractor(Boom(), position)
    try:
        raw = RawDocument(content=_html("<p>Still works.</p>"),
                          content_type="text/html", url="https://x.test/")
        chunks, doc, diag = ingest(raw)
        assert "Still works." in "\n".join(c.text for c in chunks)
        assert "boom" in doc.report.failed
        assert diag.valid
    finally:
        EXTRACTOR_REGISTRY.pop(position)


# ---------------------------------------------------------------- no hard-code

def test_extractors_are_domain_agnostic():
    # Two completely unrelated synthetic domains must pass through the same
    # generic pipeline with zero source-specific code.
    a = RawDocument(content=_html("<p>Fermion spin parity measured at 1/2.</p>"),
                    content_type="text/html", url="https://physics.test/p")
    b = RawDocument(content=b'{"recipe":{"name":"Pho","time_min":45}}',
                    content_type="application/json", url="https://cooking.test/r")
    assert "Fermion spin parity" in "\n".join(c.text for c in ingest(a)[0])
    assert "recipe.name: Pho" in "\n".join(c.text for c in ingest(b)[0])


# ---------------------------------------------------------------- regression

def test_regression_information_from_source_reaches_chunks():
    """Regression: fields visible in the source must never silently vanish."""
    source_info = "Escalation contact: oncall@example.test (24/7)."
    raw = RawDocument(content=_html(
        f"<h2>Runbook</h2><p>{source_info}</p>",
        meta='<meta name="owner" content="sre-team">', title="Runbook"),
        content_type="text/html", url="https://ops.test/runbook")
    chunks, doc, diag = ingest(raw)
    blob = "\n".join(c.text for c in chunks)
    assert source_info in blob
    assert "owner: sre-team" in blob
    assert diag.valid and not diag.warnings and diag.chunk_count > 0


def test_retriever_backward_compatible_and_prefers_ingested():
    from src.rag.retriever import retrieve_policy_snippets
    from src.rag.ingestion import index

    # Unknown query still falls back to static default snippet.
    assert retrieve_policy_snippets("totally_unknown_event")
    # Ingested doc surfaces through retrieval without breaking static behavior.
    chunks, _, _ = ingest(_html("<p>custom escalation ladder check 42</p>"),
                          content_type="text/html")
    index(chunks)
    got = retrieve_policy_snippets("custom escalation ladder check 42")
    assert any("escalation ladder check 42" in g for g in got)
    get_store().clear()
