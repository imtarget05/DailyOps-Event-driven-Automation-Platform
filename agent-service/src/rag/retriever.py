"""RAG stubs: retrieve related policies before LLM call. Vector store plugs in later."""
from __future__ import annotations


def retrieve_policy_snippets(event_type: str, top_k: int = 3) -> list[str]:
    seed = {
        "inventory_check": [
            "inventory_policy.restock_auto_threshold: restock <= 500 -> auto TICKET",
            "inventory_policy.restock_approval_threshold: restock > 500 -> EXECUTE needs approval",
        ],
        "pricing_review": ["pricing_change_approval: mọi thay đổi giá đều cần approval"],
        "refund_request": ["refund_large_approval: refund lớn cần approval"],
    }
    return seed.get(event_type, ["default: REPORT/NOTIFY/TICKET auto, EXECUTE needs approval"])[:top_k]
