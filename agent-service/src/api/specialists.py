"""Specialist + aggregate endpoints: the n8n-visible delegation surface.

- POST /agent/specialist/{name} — invoke one specialist (business_analyst,
  knowledge_agent, operations_agent, report_agent). Used by the n8n
  specialist cluster (parallel branches) and by tests/benchmarks.
- POST /agent/aggregate — merge specialist results into a canonical
  decision (used by n8n Aggregate node as reference; main path uses
  /agent/decide which already aggregates server-side).

Request body: {"correlation_id": str, "event": {"type","entity","payload"}}.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agents.base import AgentResult
from ..orchestrator import supervisor as sup

router = APIRouter()


class SpecialistRequest(BaseModel):
    correlation_id: str = ""
    event: Dict[str, Any] = Field(default_factory=dict)


class AggregateRequest(BaseModel):
    correlation_id: str = ""
    operation_type: str = "general"
    results: List[AgentResult] = Field(default_factory=list)


@router.post("/agent/specialist/{name}", response_model=AgentResult)
async def run_specialist(name: str, req: SpecialistRequest) -> AgentResult:
    runner = sup._RUNNERS.get(name)
    if runner is None:
        raise HTTPException(status_code=404, detail=f"unknown specialist: {name}")
    ev = req.event or {}
    return await runner(str(ev.get("type", "business_event")), str(ev.get("entity", "")),
                        dict(ev.get("payload", {})), req.correlation_id)


@router.post("/agent/aggregate")
async def aggregate(req: AggregateRequest) -> dict:
    agg = sup.aggregate(req.operation_type, req.results)
    return {"correlation_id": req.correlation_id, "operation_type": req.operation_type,
            "confidence": agg["confidence"], "evidence": agg["evidence"],
            "needs_more_context": agg["needs_more_context"],
            "specialists_used": [r.agent for r in req.results]}


@router.get("/agent/tools")
async def list_tools() -> dict:
    from ..tools.registry import TOOLS
    return {"tools": [{"name": t.name, "description": t.description, "category": t.category,
                       "permission": t.permission, "input_schema": t.input_schema}
                      for t in TOOLS.values()]}


@router.get("/agent/metrics")
async def metrics() -> dict:
    from ..observability.metrics import snapshot
    return snapshot()
