"""Multi-strategy, general-purpose extractors.

Design rules:
- No extractor may reference a domain, website, product, vendor format, or
  field-specific regex. They work on structural properties of the document.
- Every extractor returns blocks with provenance; independent strategies are
  combined (not short-circuited) so information is preserved, then deduplicated.
- An extractor that raises is recorded in ExtractionReport.failed — the
  pipeline continues with the remaining strategies (graceful degradation).
- Extension point: register a new Extractor via register_extractor(); the
  pipeline picks it up automatically.

Renderer limitation (documented, not faked): these extractors only see what is
present in the HTTP response. Content that requires JavaScript execution is
NOT reachable here. The pipeline detects this situation conservatively
(dynamic_content_suspected) and exposes a `renderer` extension point on the
Fetcher instead of pretending an HTML parser solved it.
"""
from __future__ import annotations

import json
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, List, Optional

from .models import (
    KIND_HEADING,
    KIND_LIST,
    KIND_METADATA,
    KIND_STRUCTURED,
    KIND_TABLE,
    ContentBlock,
    RawDocument,
)

# ---------------------------------------------------------------------------
# HTML semantic/visible-content extraction (stdlib, encoding-agnostic)


class _VisibleTextParser(HTMLParser):
    """Collects visible text while skipping script/style/noscript/head noise."""

    SKIP = {"script", "style", "noscript", "template", "head", "svg", "iframe"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._blocks: List[ContentBlock] = []
        self._buf: List[str] = []
        self._cell_buf: List[str] = []
        self._in_cell = False
        self.script_chars = 0
        self.text_chars = 0
        self.noscript_seen = False

    def _flush(self, kind: str = "text", weight: float = 1.0) -> None:
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if text:
            self._blocks.append(ContentBlock(text=text, kind=kind, weight=weight))

    def handle_starttag(self, tag: str, attrs: List) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self._skip_depth += 1
            if tag == "noscript":
                self.noscript_seen = True
            return
        if self._skip_depth:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush()
        elif tag in {"p", "div", "section", "article", "br", "li", "tr", "blockquote"}:
            self._flush()
        elif tag in {"ul", "ol"}:
            self._flush()
        elif tag == "table":
            self._flush()
        elif tag in {"td", "th"}:
            self._in_cell = True
            self._cell_buf = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush(kind=KIND_HEADING, weight=1.4)
        elif tag == "li":
            self._flush(kind=KIND_LIST)
        elif tag in {"ul", "ol"}:
            self._flush()
        elif tag in {"td", "th"}:
            self._in_cell = False
            cell = " ".join("".join(self._cell_buf).split())
            self._cell_buf = []
            if cell:
                self._blocks.append(ContentBlock(text=cell, kind=KIND_TABLE, weight=1.2))
        elif tag in {"p", "div", "section", "article", "tr", "table", "blockquote"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            if data.strip():
                self.script_chars += len(data)
            return
        if self._in_cell:
            self._cell_buf.append(data)
            self.text_chars += len(data)
            return
        if data.strip():
            self._buf.append(data)
            self.text_chars += len(data)

    def close(self) -> None:
        super().close()
        self._flush()


class SemanticHtmlExtractor:
    """Visible-text extraction that preserves headings/lists/tables as blocks."""

    name = "semantic_html"

    def extract(self, raw: RawDocument) -> List[ContentBlock]:
        ctype = (raw.content_type or "").lower()
        if "html" not in ctype and "xml" not in ctype and not raw.text.lstrip().startswith("<"):
            raise ValueError("not an HTML/XML payload")
        parser = _VisibleTextParser()
        parser.feed(raw.text)
        parser.close()
        blocks = [b for b in parser._blocks if b.text]
        for b in blocks:
            b.extractor = self.name
            b.meta["script_chars"] = parser.script_chars
            b.meta["noscript_seen"] = parser.noscript_seen
        if not blocks:
            raise ValueError("no visible text nodes found")
        return blocks


# ---------------------------------------------------------------------------
# Structured metadata: <title>, <meta>, JSON-LD

_META_NAME_RE = re.compile(r"^[a-z0-9:_\-.]+$")


class StructuredMetadataExtractor:
    """Page-level metadata: title, meta[name|property], JSON-LD blocks.

    Generic by construction: every meta tag becomes a metadata entry, JSON-LD
    is flattened as key paths — nothing vendor- or field-specific.
    """

    name = "structured_metadata"

    def extract(self, raw: RawDocument) -> List[ContentBlock]:
        blocks: List[ContentBlock] = []
        text = raw.text
        if "<" not in text:
            raise ValueError("not a markup payload")

        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        if m and m.group(1).strip():
            blocks.append(ContentBlock(
                text=" ".join(unescape(m.group(1)).split()),
                kind=KIND_METADATA, extractor=self.name, weight=1.1,
                meta={"field": "title"}))

        for m in re.finditer(
            r"<meta\s+[^>]*?(?:name|property|itemprop)\s*=\s*[\"']([^\"']+)[\"'][^>]*?"
            r"content\s*=\s*[\"']([^\"']*)[\"'][^>]*>"
            r"|"
            r"<meta\s+[^>]*?content\s*=\s*[\"']([^\"']*)[\"'][^>]*?"
            r"(?:name|property|itemprop)\s*=\s*[\"']([^\"']+)[\"'][^>]*>",
            text, re.I,
        ):
            key = (m.group(1) or m.group(4) or "").strip()
            val = " ".join(unescape(m.group(2) or m.group(3) or "").split())
            if key and val and _META_NAME_RE.match(key.lower()):
                blocks.append(ContentBlock(text=f"{key}: {val}", kind=KIND_METADATA,
                                           extractor=self.name, weight=1.1,
                                           meta={"field": key}))

        blocks.extend(self._jsonld_blocks(text))
        if not blocks:
            raise ValueError("no metadata found")
        return blocks

    def _jsonld_blocks(self, text: str) -> List[ContentBlock]:
        out: List[ContentBlock] = []
        for m in re.finditer(
            r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
            text, re.I | re.S,
        ):
            try:
                data = json.loads(m.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                out.append(ContentBlock(text="", kind=KIND_STRUCTURED,
                                        extractor=self.name,
                                        meta={"jsonld_error": "malformed"}))
                continue
            out.extend(self._flatten(data))
        return out


    def _flatten(self, data: Any, prefix: str = "") -> List[ContentBlock]:
        flat: List[ContentBlock] = []
        if isinstance(data, dict):
            for k, v in data.items():
                path = f"{prefix}.{k}" if prefix else str(k)
                if isinstance(v, (dict, list)):
                    flat.extend(self._flatten(v, path))
                elif v is not None and str(v).strip():
                    flat.append(ContentBlock(text=f"{path}: {v}".strip(),
                                             kind=KIND_STRUCTURED,
                                             extractor=self.name, weight=1.15,
                                             meta={"field": path}))
        elif isinstance(data, list):
            for item in data:
                flat.extend(self._flatten(item, prefix))
        return flat


# ---------------------------------------------------------------------------
# Native JSON payload extraction (application/json responses)


class JsonExtractor:
    """Flattens JSON documents (API responses) into key-path blocks."""

    name = "json"

    def extract(self, raw: RawDocument) -> List[ContentBlock]:
        ctype = (raw.content_type or "").lower()
        text = raw.text.lstrip()
        if "json" not in ctype and not (text.startswith("{") or text.startswith("[")):
            raise ValueError("not a JSON payload")
        data = json.loads(raw.text)
        blocks = self._flatten(data)
        if not blocks:
            raise ValueError("empty JSON payload")
        return blocks

    def _flatten(self, data: Any, prefix: str = "") -> List[ContentBlock]:
        out: List[ContentBlock] = []
        if isinstance(data, dict):
            for k, v in data.items():
                path = f"{prefix}.{k}" if prefix else str(k)
                out.extend(self._flatten(v, path))
        elif isinstance(data, list):
            if data and not any(isinstance(i, (dict, list)) for i in data):
                out.append(ContentBlock(
                    text=f"{prefix}: {', '.join(str(i) for i in data)}",
                    kind=KIND_STRUCTURED, extractor=self.name,
                    weight=1.0, meta={"field": prefix}))
            else:
                for i, item in enumerate(data):
                    out.extend(self._flatten(item, f"{prefix}[{i}]"))
        elif data is not None and str(data).strip():
            out.append(ContentBlock(text=f"{prefix}: {data}".strip(),
                                    kind=KIND_STRUCTURED, extractor=self.name,
                                    weight=1.0, meta={"field": prefix}))
        return out


# ---------------------------------------------------------------------------
# Fallback: last-resort plain-text extraction for unknown formats


class PlainTextFallbackExtractor:
    """Best-effort text salvage when every structural extractor failed.

    Conservative: strips markup-looking fragments only when the payload is
    markup; otherwise keeps the raw text. Never throws.
    """

    name = "plain_text_fallback"

    def extract(self, raw: RawDocument) -> List[ContentBlock]:
        text = raw.text
        if not text.strip():
            return []
        if text.lstrip().startswith("<"):
            # strip executable/style payloads first: they are noise, not content
            text = re.sub(r"<script[^>]*>.*?</script\s*>", " ", text, flags=re.I | re.S)
            text = re.sub(r"<style[^>]*>.*?</style\s*>", " ", text, flags=re.I | re.S)
            stripped = re.sub(r"<[^>]+>", " ", text)
            text = " ".join(unescape(stripped).split())
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return [ContentBlock(text=ln, kind="text", extractor=self.name, weight=0.5)
                for ln in lines]


# ---------------------------------------------------------------------------
# Registry + orchestration

EXTRACTOR_REGISTRY: List[Any] = [
    StructuredMetadataExtractor(),   # cheap + high-value: runs first
    SemanticHtmlExtractor(),
    JsonExtractor(),
    PlainTextFallbackExtractor(),    # always last: catch-all
]


def register_extractor(extractor: Any, position: Optional[int] = None) -> None:
    """Extension point for new general-purpose extractors."""
    if position is None:
        EXTRACTOR_REGISTRY.insert(len(EXTRACTOR_REGISTRY) - 1, extractor)
    else:
        EXTRACTOR_REGISTRY.insert(max(0, position), extractor)


_SCRIPT_SPAN_RE = re.compile(r"<(script|style)[^>]*>.*?</\1\s*>", re.I | re.S)


def _dynamic_heuristic(raw: RawDocument, report) -> None:
    """Generic client-rendering detector computed from the raw response.

    Signals: the script/style payload dominates the document while almost no
    visible markup remains, or the page ships a <noscript> hint. We flag the
    limitation; we never fabricate content an HTML parser cannot see.
    """
    text = raw.text
    script_chars = sum(len(m.group(0)) for m in _SCRIPT_SPAN_RE.finditer(text))
    visible_chars = len(_SCRIPT_SPAN_RE.sub(" ", text))
    if script_chars > 2000 and visible_chars < script_chars * 0.25:
        report.dynamic_content_suspected = True
        report.limitations.append(
            "dynamic_content: response is script-heavy with little static "
            "text; content may require JS rendering (renderer extension point)")
        return
    if re.search(r"<noscript[\s>]", text, re.I):
        report.limitations.append(
            "dynamic_content: page declares noscript fallback; some content "
            "may require JS rendering")


def extract_all(raw: RawDocument) -> tuple:
    """Run every registered extractor; combine results; never raise."""
    from .extractors import PlainTextFallbackExtractor
    from .models import ExtractionReport

    report = ExtractionReport()
    blocks: List[ContentBlock] = []
    structural_ok = False
    for extractor in EXTRACTOR_REGISTRY:
        if isinstance(extractor, PlainTextFallbackExtractor):
            # Salvage text only when structural extraction failed or covered
            # a suspiciously small share of the raw payload — never dump
            # script/style noise that primary extractors correctly discarded.
            coverage = sum(len(b.text) for b in blocks) / max(1, len(raw.text))
            if structural_ok and coverage >= 0.2:
                continue
        report.ran.append(extractor.name)
        try:
            found = extractor.extract(raw)
        except Exception as exc:  # graceful degradation, never fail the pipeline
            report.failed[extractor.name] = str(exc)
            continue
        report.succeeded.append(extractor.name)
        if not isinstance(extractor, PlainTextFallbackExtractor):
            structural_ok = True
        blocks.extend(found)

    _dynamic_heuristic(raw, report)
    if not report.succeeded:
        report.limitations.append(
            "extraction: all structural extractors failed; fallback output only")
    return blocks, report

