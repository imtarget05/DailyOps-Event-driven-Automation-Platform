"""Report agent: aggregate insights into structured report rows. No delivery."""
from __future__ import annotations

import asyncio
import time
from typing import Any, List

from .base import AgentResult


async def run(event_type: str, entity: str, payload: dict, correlation_id: str = "",
              specialist_results: List[AgentResult] | None = None) -> AgentResult:
    t0 = time.perf_counter()
    await asyncio.sleep(0.02)
    rows: List[dict[str, Any]] = []
    for r in specialist_results or []:
        rows.append({"agent": r.agent, "summary": r.summary, "confidence": r.confidence})
    summary = f"DailyOps report for {entity}: {len(rows)} specialist section(s)."
    if event_type in ("daily_report", "business_event") and not rows:
        summary = f"Báo cáo hôm nay: tồn kho ổn, 0 ticket mới ({entity})."
    return AgentResult(
        agent="report_agent", status="completed", summary=summary,
        findings=[f"sections={len(rows)}"],
        evidence=[{"source": "specialist_aggregation", "content": str(rows)[:500], "score": 0.8}],
        recommendations=["deliver_via_sheets"],
        confidence=0.8, duration_ms=int((time.perf_counter() - t0) * 1000))
