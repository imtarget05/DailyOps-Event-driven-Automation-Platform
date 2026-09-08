"""RAG stubs: retrieve related policies before LLM call. Vector store plugs in later."""
from __future__ import annotations


_ADVISORY_SNIPPETS = [
    "advisory_policy.business_grounding: tu van kinh doanh phai dua tren SOP noi bo va so lieu ton kho/doanh thu, khong bia so lieu",
    "advisory_policy.investment_grounding: tu van dau tu dai han, neu ro rui ro, khong cam ket loi nhuan, chi REPORT khong EXECUTE",
    "advisory_policy.marketing_grounding: tu van marketing dua tren ROAS va ngan sach thuc te, trich nguon du lieu",
    "advisory_policy.citation_required: moi khuyen nghi phai trich nguon policy/SOP, thieu nguon -> needs_context",
]

_ADVISORY_KEYWORDS = (
    "tu van", "tư vấn", "advisory", "co van", "cố vấn", "hoi dong", "hội đồng",
    "chien luoc", "chiến lược", "strategy", "hormozi", "buffett", "gary",
    "kinh doanh", "dau tu", "đầu tư", "marketing", "offers", "growth",
)


def _advisory_match(query: str) -> bool:
    q = (query or "").lower()
    return any(k in q for k in _ADVISORY_KEYWORDS)


def retrieve_policy_snippets(event_type: str, top_k: int = 3) -> list[str]:
    """Retrieval order (backward compatible):
    1. Documents ingested through src.rag.ingestion (vector store) — preferred.
    2. Legacy seed/keyword snippets — static fallback when nothing (or too
       little) has been ingested.
    """
    results: list[str] = []
    try:
        from .ingestion import get_store

        hits = get_store().search(event_type, top_k=top_k)
        results = [chunk.text for chunk, _score in hits]
    except Exception:
        results = []
    if len(results) >= top_k:
        return results[:top_k]
    seed = {

        "inventory_check": [
            "inventory_policy.restock_auto_threshold: restock <= 500 -> auto TICKET",
            "inventory_policy.restock_approval_threshold: restock > 500 -> EXECUTE needs approval",
        ],
        "pricing_review": ["pricing_change_approval: mọi thay đổi giá đều cần approval"],
        "refund_request": ["refund_large_approval: refund lớn cần approval"],
        "advisory_request": _ADVISORY_SNIPPETS,
        "business_strategy": _ADVISORY_SNIPPETS,
        "investment": _ADVISORY_SNIPPETS,
        "marketing": _ADVISORY_SNIPPETS,
    }
    static = None
    if event_type in seed:
        static = seed[event_type][:top_k]
    elif _advisory_match(event_type):
        # Keyword fallback: advisory queries get grounding docs even when
        # event_type is generic (e.g. telegram_chat + "tu van ...").
        static = _ADVISORY_SNIPPETS[:top_k]
    else:
        static = seed.get(event_type, ["default: REPORT/NOTIFY/TICKET auto, EXECUTE needs approval"])[:top_k]
    # Ingested evidence first, static policy snippets fill the remainder.
    merged = list(dict.fromkeys(results + static))
    return merged[:top_k]
