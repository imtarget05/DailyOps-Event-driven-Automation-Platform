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
    # DAILYOPS-01: every tool must carry one of RISK_LEVELS (4-group taxonomy).
    risk_level: str = "EXTERNAL_SIDE_EFFECT"  # fail-safe default: highest risk


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
                               {"entity": "string", "window_days": "int"}, get_sales_data,
                               risk_level="READ"),
    "get_inventory": ToolSpec("get_inventory", "Read current inventory level", "READ", "auto",
                              {"entity": "string"}, get_inventory, risk_level="READ"),
    "get_order_data": ToolSpec("get_order_data", "Read open/recent orders", "READ", "auto",
                               {"entity": "string", "window_days": "int"}, get_order_data,
                               risk_level="READ"),
    "get_marketing_metrics": ToolSpec("get_marketing_metrics", "Read ad spend/ROAS", "READ", "auto",
                                      {"entity": "string"}, get_marketing_metrics, risk_level="READ"),
    "search_business_policy": ToolSpec("search_business_policy", "RAG retrieval over policy/SOP docs", "READ", "auto",
                                       {"query": "string", "top_k": "int"}, search_business_policy,
                                       risk_level="READ"),
    "get_customer_context": ToolSpec("get_customer_context", "Read CRM segment/context", "READ", "auto",
                                     {"entity": "string"}, get_customer_context, risk_level="READ"),
    # Descriptors: proposed by agents, executed by n8n after policy gate.
    "append_google_sheet": ToolSpec("append_google_sheet", "Append a report row to Google Sheets", "OUTPUT", "auto",
                                    {"row": "object"}, risk_level="LOW-RISK_WRITE"),
    "send_telegram": ToolSpec("send_telegram", "Send a Telegram message", "OUTPUT", "auto",
                              {"chat_id": "int", "text": "string"}, risk_level="LOW-RISK_WRITE"),
    "send_email": ToolSpec("send_email", "Send an email notification", "OUTPUT", "auto",
                           {"to": "string", "subject": "string", "body": "string"}, risk_level="LOW-RISK_WRITE"),
    "create_ticket": ToolSpec("create_ticket", "Create an ops ticket (Postgres)", "OUTPUT", "auto",
                              {"type": "string", "priority": "string", "entity": "string"},
                              risk_level="LOW-RISK_WRITE"),
    "update_ticket": ToolSpec("update_ticket", "Update ticket status", "OUTPUT", "auto",
                              {"ticket_id": "string", "status": "string"}, risk_level="LOW-RISK_WRITE"),
    "create_restock_request": ToolSpec("create_restock_request", "Propose a restock (<=500 auto, >500 approval)", "ACTION", "approval",
                                       {"entity": "string", "qty": "int"}, risk_level="HIGH-RISK_WRITE"),
    "update_campaign": ToolSpec("update_campaign", "Propose a campaign change (approval)", "ACTION", "approval",
                                {"campaign": "string", "change": "object"}, risk_level="HIGH-RISK_WRITE"),
    "update_crm_record": ToolSpec("update_crm_record", "Propose a CRM update (approval for sensitive)", "ACTION", "approval",
                                  {"entity": "string", "fields": "object"}, risk_level="HIGH-RISK_WRITE"),
    # EXECUTE-class: touches an external API/ERP — highest risk group.
    "execute_action": ToolSpec("execute_action", "Execute a business action on the target API (approval-gated)",
                               "ACTION", "approval", {"correlation_id": "string", "action": "string"},
                               risk_level="EXTERNAL_SIDE_EFFECT"),
}


def list_tools(category: str | None = None) -> List[ToolSpec]:
    return [t for t in TOOLS.values() if category is None or t.category == category]
