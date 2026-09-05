"""POST /agent/decide — single contract between n8n and agent-service.

v3: delegates to the Supervisor (classify -> parallel specialists ->
aggregate -> policy gate). Response keeps the v2 fields for backward
compatibility and adds supervisor trace fields (decision_id,
operation_type, specialists_used, evidence, action, failure_kind).
"""
from __future__ import annotations

from fastapi import APIRouter

from ..orchestrator.supervisor import supervise
from ..schemas.models import (
    ActionProposal,
    DecideRequest,
    DecideResponse,
    DecisionDetail,
    EvidenceItem,
    TicketPayload,
)

router = APIRouter()


@router.post("/agent/decide", response_model=DecideResponse)
async def decide(req: DecideRequest) -> DecideResponse:
    s = await supervise(req.correlation_id, req.event.type, req.event.entity,
                        req.event.payload, req.context.model_dump())

    ticket = None
    if s["action_type"] == "TICKET":
        ticket = TicketPayload(
            type=s.get("ticket_type", "general"),
            priority="high" if "nguy cơ" in s["summary"] or "HIGH" in s["summary"] else "medium",
            entity=req.event.entity,
            recommendation=s.get("recommended_action", ""),
        )
    return DecideResponse(
        correlation_id=req.correlation_id,
        decision=DecisionDetail(summary=s["summary"], reasoning=s.get("reasoning", ""),
                                confidence=float(s.get("confidence", 0.0))),
        action_type=s["action_type"],
        recommended_action=s.get("recommended_action", ""),
        needs_approval=s.get("needs_approval", False),
        policy_matched=s.get("policy_matched", ""),
        ticket_payload=ticket,
        decision_id=s.get("decision_id", ""),
        operation_type=s.get("operation_type", "general"),
        priority=s.get("priority", "MEDIUM"),
        requires_approval=s.get("needs_approval", False),
        specialists_used=s.get("specialists_used", []),
        evidence=[EvidenceItem(**e) for e in s.get("evidence", [])],
        action=ActionProposal(**s.get("action", {"type": "send_message", "parameters": {}})),
        failure_kind=s.get("failure_kind", "SUCCESS"),
        supervisor_latency_ms=s.get("supervisor_latency_ms", 0),
    )
