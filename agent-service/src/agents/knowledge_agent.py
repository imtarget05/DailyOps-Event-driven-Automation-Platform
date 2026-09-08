"""Knowledge / Policy agent: Agentic RAG with sufficiency loop and citations."""
from __future__ import annotations

import asyncio
import time

from .base import AgentResult
from ..rag.retriever import retrieve_policy_snippets


def _sufficient(snippets: list[str], query: str) -> bool:
    if not snippets:
        return False
    q = query.lower()
    blob = " ".join(snippets).lower()
    keys = [k for k in ("restock", "refund", "pricing", "report", "notify", "ticket", "approval", "inventory") if k in q]
    if not keys:
        return True  # generic query: default policy snippet is enough
    return any(k in blob for k in keys)


async def run(event_type: str, entity: str, payload: dict, correlation_id: str = "") -> AgentResult:
    t0 = time.perf_counter()
    await asyncio.sleep(0.02)
    query = event_type
    text = str(payload.get("text", ""))
    if text:
        query = f"{event_type} {text[:120]}"
    snippets = retrieve_policy_snippets(query, top_k=3)
    rounds = 1
    # Agentic loop: retry with broader query if evidence insufficient (max 2 rounds).
    if not _sufficient(snippets, query):
        await asyncio.sleep(0.01)
        broader = retrieve_policy_snippets(event_type, top_k=3)
        snippets = list(dict.fromkeys(snippets + broader))[:3]
        rounds = 2
    ok = _sufficient(snippets, query)
    # Grounding from the real ingestion pipeline: SOP documents indexed in the
    # vector store (multi-strategy extraction -> canonical docs -> chunks).
    kb_hits = []
    try:
        from ..rag.knowledge_base import ensure_indexed
        from ..rag.ingestion import get_store
        ensure_indexed()
        kb_hits = [chunk.text for chunk, _s in get_store().search(query, top_k=2)]
    except Exception:
        kb_hits = []
    if kb_hits and not ok:
        ok = True  # ingested SOP evidence satisfies the sufficiency check
    evidence = ([{"source": "knowledge_base", "content": h, "score": 0.95 - i * 0.05}
                 for i, h in enumerate(kb_hits)]
                + [{"source": "policy_rag", "content": s, "score": 0.9 - i * 0.05}
                   for i, s in enumerate(snippets)])
    return AgentResult(
        agent="knowledge_agent", status="completed" if ok else "needs_context",
        summary=f"policy evidence ({len(snippets)} snippets, {len(kb_hits)} doc hits, {rounds} retrieval round(s))",
        findings=snippets,
        evidence=evidence,
        recommendations=[] if ok else ["request_more_policy_docs"],
        confidence=0.85 if ok else 0.4,
        requires_more_context=not ok,
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )
