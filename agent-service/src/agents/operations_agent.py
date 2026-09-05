"""Operations agent: inspect state, propose (never execute) safe business operations."""
from __future__ import annotations

import asyncio
import time

from .base import AgentResult
from ..tools import registry as tools

DENY_KEYWORDS = ("delete", "drop", "truncate", "shutdown", "rm -rf", "destroy")


async def run(event_type: str, entity: str, payload: dict, correlation_id: str = "") -> AgentResult:
    t0 = time.perf_counter()
    await asyncio.sleep(0.02)
    text = str(payload.get("text", "")).lower()
    recommended = str(payload.get("recommended_action", "") or "")
    if any(k in text for k in DENY_KEYWORDS) or any(k in recommended.lower() for k in DENY_KEYWORDS):
        return AgentResult(
            agent="operations_agent", status="completed",
            summary="destructive operation proposed -> must be DENIED by policy",
            findings=["prohibited_action_detected"], evidence=[],
            recommendations=["deny"], confidence=0.99,
            duration_ms=int((time.perf_counter() - t0) * 1000))
    orders = tools.get_order_data(entity)
    customer = tools.get_customer_context(entity)
    # Propose, do not execute.
    if event_type == "inventory_check" or "restock" in text or "kho" in text or "hết hàng" in text:
        proposal = "create_restock_request(entity=%s, qty=200)" % entity
    elif "refund" in text or "hoàn tiền" in text:
        proposal = "refund_order (requires approval)"
    elif "giá" in text or "price" in text:
        proposal = "pricing_change (requires approval)"
    else:
        proposal = "no_action_required"
    return AgentResult(
        agent="operations_agent", status="completed",
        summary=f"ops state inspected; proposal: {proposal}",
        findings=[f"open_orders={orders.get('open_orders')} segment={customer.get('segment')}"],
        evidence=[{"source": "get_order_data", "content": str(orders), "score": 0.8},
                  {"source": "get_customer_context", "content": str(customer), "score": 0.7}],
        recommendations=[proposal], confidence=0.8,
        duration_ms=int((time.perf_counter() - t0) * 1000))
