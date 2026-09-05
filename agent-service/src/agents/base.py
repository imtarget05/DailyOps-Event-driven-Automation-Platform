"""Structured inter-agent contract. No free-form text between agents."""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field

AgentStatus = Literal["completed", "failed", "needs_context"]


class AgentResult(BaseModel):
    agent: str
    task_id: str = Field(default_factory=lambda: "task_" + uuid.uuid4().hex[:8])
    status: AgentStatus = "completed"
    summary: str = ""
    findings: List[str] = Field(default_factory=list)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    requires_more_context: bool = False
    duration_ms: int = 0
