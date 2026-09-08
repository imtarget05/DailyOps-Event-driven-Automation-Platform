"""Knowledge / Policy agent: Agentic RAG with sufficiency loop and citations.

Tunables are read from policies.yaml (ARCH-004 / TASK-006); defaults match the
former hardcoded values, so behavior is identical unless overridden.
"""
from __future__ import annotations

import asyncio
import os
import time

import yaml

from ..rag.retriever import retrieve_policy_snippets
from .base import AgentResult

# ARCH-004: defaults used when policies.yaml is missing/unparseable.
_DEF_POLICY = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "guardrails", "policies.yaml"))
_KNOWLEDGE_DEFAULTS = {
    "top_k": 3,
    "snippet_limit": 3,
    "kb_top_k": 2,
    "max_rounds": 2,
    "text_preview": 120,
    "kb_score_base": 0.95,
    "policy_score_base": 0.9,
    "score_decay": 0.05,
    "confidence_ok": 0.85,
    "confidence_missing": 0.4,
    "sufficient_terms": ["restock", "refund", "pricing", "report", "notify",
                         "ticket", "approval", "inventory"],
}


def _load_knowledge_config(path: str | None = None) -> dict:
    """Read the `knowledge_agent:` section of policies.yaml; fallback to defaults."""
    cfg = dict(_KNOWLEDGE_DEFAULTS)
    p = path or os.environ.get("POLICY_FILE", _DEF_POLICY)
    try:
        with open(p, encoding="utf-8") as f:
            section = (yaml.safe_load(f) or {}).get("knowledge_agent", {}) or {}
        if isinstance(section, dict):
            cfg.update({k: v for k, v in section.items() if k in _KNOWLEDGE_DEFAULTS})
    except (FileNotFoundError, ValueError, TypeError):
        pass
    return cfg


def _sufficient(snippets: list[str], query: str, terms: list[str]) -> bool:
    if not snippets:
        return False
    q = query.lower()
    blob = " ".join(snippets).lower()
    keys = [k for k in terms if k in q]
    if not keys:
        return True  # generic query: default policy snippet is enough
    return any(k in blob for k in keys)


async def run(event_type: str, entity: str, payload: dict, correlation_id: str = "") -> AgentResult:
    t0 = time.perf_counter()
    await asyncio.sleep(0.02)
    cfg = _load_knowledge_config()
    query = event_type
    text = str(payload.get("text", ""))
    if text:
        query = f"{event_type} {text[:cfg['text_preview']]}"
    snippets = retrieve_policy_snippets(query, top_k=cfg["top_k"])
    rounds = 1
    # Agentic loop: retry with broader query if evidence insufficient (max `max_rounds`).
    if not _sufficient(snippets, query, cfg["sufficient_terms"]):
        await asyncio.sleep(0.01)
        broader = retrieve_policy_snippets(event_type, top_k=cfg["top_k"])
        snippets = list(dict.fromkeys(snippets + broader))[:cfg["snippet_limit"]]
        rounds = min(cfg["max_rounds"], 2)
    ok = _sufficient(snippets, query, cfg["sufficient_terms"])
    # Grounding from the real ingestion pipeline: SOP documents indexed in the
    # vector store (multi-strategy extraction -> canonical docs -> chunks).
    kb_hits = []
    try:
        from ..rag.ingestion import get_store
        from ..rag.knowledge_base import ensure_indexed
        ensure_indexed()
        kb_hits = [chunk.text for chunk, _s in get_store().search(query, top_k=cfg["kb_top_k"])]
    except Exception:
        kb_hits = []
    if kb_hits and not ok:
        ok = True  # ingested SOP evidence satisfies the sufficiency check
    decay = cfg["score_decay"]
    evidence = ([{"source": "knowledge_base", "content": h, "score": cfg["kb_score_base"] - i * decay}
                 for i, h in enumerate(kb_hits)]
                + [{"source": "policy_rag", "content": s, "score": cfg["policy_score_base"] - i * decay}
                   for i, s in enumerate(snippets)])
    return AgentResult(
        agent="knowledge_agent", status="completed" if ok else "needs_context",
        summary=f"policy evidence ({len(snippets)} snippets, {len(kb_hits)} doc hits, {rounds} retrieval round(s))",
        findings=snippets,
        evidence=evidence,
        recommendations=[] if ok else ["request_more_policy_docs"],
        confidence=cfg["confidence_ok"] if ok else cfg["confidence_missing"],
        requires_more_context=not ok,
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )
