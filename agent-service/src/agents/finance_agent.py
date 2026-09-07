"""Finance agent: refund/dispute/cashflow — deterministic risk metrics.

READ-only: inspects order data and classifies refund risk. It PROPOSES only;
execution rights stay with the Policy Engine (large refunds -> APPROVAL).
"""
from __future__ import annotations

import asyncio
import time

from .base import AgentResult
from ..guardrails.policy_engine import _load_thresholds
from ..tools import registry as tools

_thr = _load_thresholds()
_HIGH_RISK_AMOUNT = float(_thr["finance_refund_auto_max"])  # single source: policies.yaml
_MEDIUM_RISK_AMOUNT = _HIGH_RISK_AMOUNT * 0.2


async def run(event_type: str, entity: str, payload: dict, correlation_id: str = "") -> AgentResult:
    t0 = time.perf_counter()
    await asyncio.sleep(0.02)
    try:
        amount = float(payload.get("refund_amount") or payload.get("amount") or 0)
    except (TypeError, ValueError):
        return AgentResult(agent="finance_agent", status="failed",
                           summary="invalid refund amount", confidence=0.0,
                           duration_ms=int((time.perf_counter() - t0) * 1000))
    order = tools.get_order_data(entity)
    open_orders = int(order.get("open_orders", 0))
    risk = "HIGH" if amount > _HIGH_RISK_AMOUNT else (
        "MEDIUM" if amount > _MEDIUM_RISK_AMOUNT else "LOW")
    if open_orders > 5:
        risk = "HIGH"  # dispute-prone account: always escalate
    proposal = "refund_request_approval" if risk != "LOW" else "refund_auto_note"
    return AgentResult(
        agent="finance_agent", status="completed",
        summary=f"{entity}: refund={amount:.2f} open_orders={open_orders} -> risk {risk}",
        findings=[f"refund_amount={amount:.2f}",
                  f"risk_level={risk} high>{_HIGH_RISK_AMOUNT:g} medium>{_MEDIUM_RISK_AMOUNT:g}",
                  f"open_orders={open_orders}"],
        evidence=[{"source": "get_order_data", "content": f"open_orders={open_orders}", "score": 1.0},
                  {"source": "refund_request", "content": f"amount={amount:.2f}", "score": 1.0}],
        recommendations=[proposal],
        confidence=0.88 if risk != "LOW" else 0.7,
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )
