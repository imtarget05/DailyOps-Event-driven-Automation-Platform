"""Advisory Council agent: 3 personas with mandatory grounding check.

Personas (from product screenshots):
- Alex Hormozi  -> Business (Offers & Growth, Scaling Systems, Value Creation)
- Warren Buffett -> Stocks (Long-Term Investing, Market Trends, Risk Management)
- Gary Vee      -> Finance/Marketing (Cash Flow, Brand & Content, Attention Leverage)

Anti-hallucination rule (Phase 2 grounding):
- Every persona advice MUST cite retrieved policy/SOP snippets.
- If RAG snippets are irrelevant (e.g. default fallback for crypto-moon
  questions), grounding_score drops below threshold -> status needs_context,
  low confidence, disclaimer prefix. Supervisor then lowers overall
  confidence and keeps action REPORT/NOTIFY only (never TICKET/EXECUTE).
"""
from __future__ import annotations

import asyncio
import os
import re
import time

import yaml

from ..rag.retriever import retrieve_policy_snippets
from .base import AgentResult

GROUNDING_THRESHOLD = 0.12
UNGROUNDED_CONFIDENCE = 0.35
GROUNDED_CONFIDENCE = 0.78

_DEFAULT_POLICY = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "guardrails", "policies.yaml"))
# ARCH-005: tunables read from policies.yaml `advisory_council:` section.
# Defaults match the former hardcoded values (behavior-preserving).
_ADVISORY_DEFAULTS = {
    "grounding_threshold": GROUNDING_THRESHOLD,
    "ungrounded_confidence": UNGROUNDED_CONFIDENCE,
    "grounded_confidence": GROUNDED_CONFIDENCE,
    "top_k": 3,
    "snippet_limit": 3,
    "text_query_preview": 120,
    "question_preview": 80,
    "cite_preview": 90,
    "evidence_preview": 300,
    "recommendation_preview": 120,
    "evidence_score_base": 0.9,
    "score_decay": 0.05,
}


def _load_advisory_config(path: str | None = None) -> dict:
    """Read the `advisory_council:` section of policies.yaml; fallback to defaults."""
    cfg = dict(_ADVISORY_DEFAULTS)
    p = path or os.environ.get("POLICY_FILE", _DEFAULT_POLICY)
    try:
        with open(p, encoding="utf-8") as f:
            section = (yaml.safe_load(f) or {}).get("advisory_council", {}) or {}
        if isinstance(section, dict):
            for k, v in section.items():
                if k in _ADVISORY_DEFAULTS:
                    try:
                        if isinstance(_ADVISORY_DEFAULTS[k], (int, float)):
                            cfg[k] = type(_ADVISORY_DEFAULTS[k])(v)
                        else:
                            cfg[k] = v
                    except (TypeError, ValueError):
                        pass
    except (FileNotFoundError, ValueError, TypeError):
        pass
    return cfg

PERSONAS = {
    "alex_hormozi": {
        "label": "ALEX HORMOZI",
        "domain": "Business",
        "expertise": ("Offers & Growth", "Scaling Systems", "Value Creation"),
    },
    "warren_buffett": {
        "label": "WARREN BUFFETT",
        "domain": "Stocks",
        "expertise": ("Long-Term Investing", "Market Trends", "Risk Management"),
    },
    "gary_vee": {
        "label": "GARY VEE",
        "domain": "Finance",
        "expertise": ("Cash Flow", "Brand & Content", "Attention Leverage"),
    },
}

_TOKEN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)

_STOPWORDS = frozenset({
    "va", "and", "the", "voi", "với", "trong", "cho", "cua", "của", "mot",
    "một", "cac", "các", "thi", "thì", "la", "là", "khong", "không", "can",
    "cần", "ban", "bạn", "anh", "chi", "chị", "nhe", "nhé", "nay", "nay",
    "day", "đây", "nay", "for", "with", "from", "that", "this", "have",
})


def _tokens(text: str) -> set[str]:
    toks = {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= 4}
    return {t for t in toks if t not in _STOPWORDS}


def grounding_score(advice_text: str, snippets: list[str]) -> float:
    """Token-overlap between advice and retrieved evidence (0..1)."""
    advice_toks = _tokens(advice_text)
    if not advice_toks or not snippets:
        return 0.0
    blob_toks = _tokens(" ".join(snippets))
    if not blob_toks:
        return 0.0
    return len(advice_toks & blob_toks) / max(1, len(advice_toks))


def _persona_advice(persona_key: str, question: str, snippets: list[str], cfg: dict) -> str:
    """Deterministic template that CITES evidence (keeps grounding high)."""
    meta = PERSONAS[persona_key]
    cite = snippets[0][:cfg["cite_preview"]] if snippets else "no-evidence"
    expertise = ", ".join(meta["expertise"])
    # Include snippet keywords verbatim so overlap reflects real citation.
    return (
        f"[{meta['label']} | {meta['domain']}: {expertise}] "
        f"Goc nhin cho '{question[:cfg['question_preview']]}': "
        f"(1) doi chieu SOP/noi bo truoc khi hanh dong; "
        f"(2) neu ro rui ro va dieu kien ap dung; "
        f"(3) buoc tiep theo nho, do duoc. "
        f"[nguon: {cite}]"
    )


async def run(event_type: str, entity: str, payload: dict, correlation_id: str = "") -> AgentResult:
    t0 = time.perf_counter()
    await asyncio.sleep(0.02)
    cfg = _load_advisory_config()
    text = str(payload.get("text", "") or payload.get("question", "") or "").strip()
    question = text if text else event_type

    query = f"{event_type} {question[:cfg['text_query_preview']]}".strip()
    snippets = retrieve_policy_snippets(query, top_k=cfg["top_k"])
    if len(snippets) < cfg["top_k"]:
        # Broaden once, same pattern as knowledge_agent (max 2 rounds).
        broader = retrieve_policy_snippets(event_type, top_k=cfg["top_k"])
        snippets = list(dict.fromkeys(snippets + broader))[:cfg["snippet_limit"]]

    findings: list[str] = []
    evidence: list[dict] = []
    recommendations: list[str] = []
    scores: list[float] = []

    for key in PERSONAS:
        advice = _persona_advice(key, question, snippets, cfg)
        score = grounding_score(advice, snippets)
        scores.append(score)
        findings.append(f"{key}: {advice}")
        evidence.append({"source": f"advisory_{key}",
                         "content": advice[:cfg["evidence_preview"]], "score": round(score, 3)})
        recommendations.append(f"advisory_{key}_consider: {advice[:cfg['recommendation_preview']]}")

    # Attach raw RAG evidence so supervisor/n8n can audit citations.
    for i, s in enumerate(snippets):
        evidence.append({"source": "policy_rag", "content": s[:cfg["evidence_preview"]],
                         "score": cfg["evidence_score_base"] - i * cfg["score_decay"]})

    mean_score = sum(scores) / len(scores) if scores else 0.0
    # Anti-fake-citation: template always cites snippets, so advice_score
    # alone is insufficient. Require the QUESTION itself to overlap RAG
    # (i.e. internal docs actually cover the asked topic).
    gt = cfg["grounding_threshold"]
    question_score = grounding_score(question, snippets)
    grounded = bool(snippets) and mean_score >= gt and question_score >= gt

    if grounded:
        return AgentResult(
            agent="advisory_council", status="completed",
            summary=(
                f"Advisory council (3 personas, grounding={mean_score:.2f}, "
                f"qcov={question_score:.2f}): Hormozi/Business + Buffett/Stocks "
                f"+ GaryVee/Finance, co trich nguon SOP."
            ),
            findings=findings,
            evidence=evidence,
            recommendations=recommendations,
            confidence=cfg["grounded_confidence"],
            requires_more_context=False,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    return AgentResult(
        agent="advisory_council", status="needs_context",
        summary=(
            f"[Chua du du lieu noi bo - grounding={mean_score:.2f}, qcov={question_score:.2f}] "
            f"Cau hoi '{question[:cfg['question_preview']]}' chua co SOP/so lieu doi chieu. "
            f"Day la goc nhin tham khao, khong thay the quyet dinh. "
            f"Can bo sung SOP/doanh thu/ROAS de tang do tin cay."
        ),
        findings=findings,
        evidence=evidence,
        recommendations=["request_more_docs: SOP/doanh thu/ROAS lien quan", *recommendations],
        confidence=cfg["ungrounded_confidence"],
        requires_more_context=True,
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )
