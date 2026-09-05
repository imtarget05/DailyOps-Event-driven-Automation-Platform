"""Shared schemas: single definition used by n8n <-> agent-service contract.

v3 (multi-agent orchestrator): backward-compatible extension of the v2
DecideRequest/DecideResponse contract. Old clients reading only
(correlation_id, decision, action_type, recommended_action, needs_approval,
policy_matched, ticket_payload) keep working; new fields carry the
supervisor/specialist trace (decision_id, operation_type, specialists_used,
evidence, action, failure classification).
"""
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

ActionType = Literal["REPORT", "NOTIFY", "TICKET", "EXECUTE"]
FailureKind = Literal["SUCCESS", "FAILED", "RETRYABLE", "NON_RETRYABLE", "APPROVAL_REQUIRED", "DENIED"]


class Event(BaseModel):
    type: str
    entity: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


class DecideContext(BaseModel):
    history_window_days: int = 30
    requested_by: str = "daily-report-workflow"


class DecideRequest(BaseModel):
    correlation_id: str
    event: Event
    context: DecideContext = Field(default_factory=DecideContext)


class DecisionDetail(BaseModel):
    summary: str
    reasoning: str = ""
    confidence: float = 0.0


class TicketPayload(BaseModel):
    type: str = "general"
    priority: str = "medium"
    entity: str = ""
    recommendation: str = ""


class EvidenceItem(BaseModel):
    source: str = ""
    content: str = ""
    score: float = 0.0


class ActionProposal(BaseModel):
    type: str = "create_ticket"
    parameters: Dict[str, Any] = Field(default_factory=dict)


class DecideResponse(BaseModel):
    correlation_id: str
    decision: DecisionDetail
    action_type: ActionType
    recommended_action: str = ""
    needs_approval: bool = False
    policy_matched: str = ""
    ticket_payload: Optional[TicketPayload] = None
    # --- v3 supervisor extensions (all optional => backward compatible) ---
    decision_id: str = ""
    operation_type: str = "general"
    priority: str = "MEDIUM"
    requires_approval: bool = False
    specialists_used: List[str] = Field(default_factory=list)
    evidence: List[EvidenceItem] = Field(default_factory=list)
    action: Optional[ActionProposal] = None
    failure_kind: FailureKind = "SUCCESS"
    supervisor_latency_ms: int = 0
