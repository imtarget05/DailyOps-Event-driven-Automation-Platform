"""Explicit tool registry: name, description, typed I/O, error behavior, permission class.

READ tools execute inside agent-service (no side effects).
OUTPUT/ACTION tools are DESCRIPTORS only — agent-service may PROPOSE them,
n8n executes them after the Policy Engine gate. LLM never calls them directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


@dataclass
class ToolSpec:
    name: str
    description: str
    category: str  # READ | OUTPUT | ACTION
    permission: str  # auto | approval | deny
    input_schema: Dict[str, Any] = field(default_factory=dict)
    handler: Callable[..., Dict[str, Any]] | None = None


def _ok(**kw: Any) -> Dict[str, Any]:
    return {"status": "completed", **kw}


def get_sales_data(entity: str, window_days: int = 30) -> Dict[str, Any]:
    try:
        return _ok(entity=entity, total_units=240, avg_daily_sales=8.0,
                   window_days=window_days, source="sheets_stub")
    except Exception as exc:  # pragma: no cover
        return {"status": "failed", "error": str(exc), "retryable": True}


def get_inventory(entity: str) -> Dict[str, Any]:
    try:
        return _ok(entity=entity, stock=15, source="sheets_stub")
    except Exception as exc:  # pragma: no cover
        return {"status": "failed", "error": str(exc), "retryable": True}


def get_order_data(entity: str, window_days: int = 30) -> Dict[str, Any]:
    try:
        return _ok(entity=entity, open_orders=3, window_days=window_days, source="erp_stub")
    except Exception as exc:  # pragma: no cover
        return {"status": "failed", "error": str(exc), "retryable": True}


def get_marketing_metrics(entity: str) -> Dict[str, Any]:
    try:
        return _ok(entity=entity, spend=120.0, roas=2.4, source="ads_stub")
    except Exception as exc:  # pragma: no cover
        return {"status": "failed", "error": str(exc), "retryable": True}


def get_customer_context(entity: str) -> Dict[str, Any]:
    try:
        return _ok(entity=entity, segment="unknown", source="crm_stub")
    except Exception as exc:  # pragma: no cover
        return {"status": "failed", "error": str(exc), "retryable": True}


def search_business_policy(query: str, top_k: int = 3) -> Dict[str, Any]:
    from ..rag.retriever import retrieve_policy_snippets
    try:
        return _ok(query=query, snippets=retrieve_policy_snippets(query, top_k))
    except Exception as exc:  # pragma: no cover
        return {"status": "failed", "error": str(exc), "retryable": True}


TOOLS: Dict[str, ToolSpec] = {
    "get_sales_data": ToolSpec("get_sales_data", "Read sales aggregates for an entity", "READ", "auto",
                               {"entity": "string", "window_days": "int"}, get_sales_data),
    "get_inventory": ToolSpec("get_inventory", "Read current inventory level", "READ", "auto",
                              {"entity": "string"}, get_inventory),
    "get_order_data": ToolSpec("get_order_data", "Read open/recent orders", "READ", "auto",
                               {"entity": "string", "window_days": "int"}, get_order_data),
    "get_marketing_metrics": ToolSpec("get_marketing_metrics", "Read ad spend/ROAS", "READ", "auto",
                                      {"entity": "string"}, get_marketing_metrics),
    "search_business_policy": ToolSpec("search_business_policy", "RAG retrieval over policy/SOP docs", "READ", "auto",
                                       {"query": "string", "top_k": "int"}, search_business_policy),
    "get_customer_context": ToolSpec("get_customer_context", "Read CRM segment/context", "READ", "auto",
                                     {"entity": "string"}, get_customer_context),
    # Descriptors: proposed by agents, executed by n8n after policy gate.
    "append_google_sheet": ToolSpec("append_google_sheet", "Append a report row to Google Sheets", "OUTPUT", "auto",
                                    {"row": "object"}),
    "send_telegram": ToolSpec("send_telegram", "Send a Telegram message", "OUTPUT", "auto",
                              {"chat_id": "int", "text": "string"}),
    "send_email": ToolSpec("send_email", "Send an email notification", "OUTPUT", "auto",
                           {"to": "string", "subject": "string", "body": "string"}),
    "create_ticket": ToolSpec("create_ticket", "Create an ops ticket (Postgres)", "OUTPUT", "auto",
                              {"type": "string", "priority": "string", "entity": "string"}),
    "update_ticket": ToolSpec("update_ticket", "Update ticket status", "OUTPUT", "auto",
                              {"ticket_id": "string", "status": "string"}),
    "create_restock_request": ToolSpec("create_restock_request", "Propose a restock (<=500 auto, >500 approval)", "ACTION", "approval",
                                       {"entity": "string", "qty": "int"}),
    "update_campaign": ToolSpec("update_campaign", "Propose a campaign change (approval)", "ACTION", "approval",
                                {"campaign": "string", "change": "object"}),
    "update_crm_record": ToolSpec("update_crm_record", "Propose a CRM update (approval for sensitive)", "ACTION", "approval",
                                  {"entity": "string", "fields": "object"}),
}


def list_tools(category: str | None = None) -> List[ToolSpec]:
    return [t for t in TOOLS.values() if category is None or t.category == category]
