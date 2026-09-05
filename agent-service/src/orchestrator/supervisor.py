"""DailyOps Supervisor: classify -> delegate (asyncio.gather) -> aggregate -> decision.

Supervisor NEVER performs side effects. It only plans delegation, runs
specialist agents concurrently, aggregates structured AgentResults, and
produces a canonical decision consumed by n8n + Policy Engine.
"""
from __future__ import annotations

import asyncio
import time
import uuid

from ..agents import business_analyst, knowledge_agent, operations_agent, report_agent
from ..agents.base import AgentResult
from ..guardrails.policy_engine import check
from ..memory.store import memory_key, recall, remember
from ..observability.metrics import observe
from ..reasoning.llm_client import reason

ANALYST_EVENTS = {"inventory_check", "business_event", "daily_report", "sales_review"}
ALL_SPECIALISTS = ("business_analyst", "knowledge_agent", "operations_agent", "report_agent")


def classify_operation(event_type: str, payload: dict) -> str:
    text = str(payload.get("text", "")).lower()
    if event_type == "inventory_check" or any(k in text for k in ("tồn kho", "hết hàng", "restock", "kho")):
        return "inventory_analysis"
    if event_type == "telegram_chat":
        if any(k in text for k in ("báo cáo", "report", "tổng hợp", "thống kê")):
            return "daily_report"
        if any(k in text for k in ("hoàn tiền", "refund", "đổi giá", "giá")):
            return "approval_action"
        if any(k in text for k in ("chào", "hello", "hi", "cảm ơn", "tạm biệt")) or len(text.strip()) < 12:
            return "smalltalk"
        return "general_request"
    if event_type in ("daily_report", "schedule"):
        return "daily_report"
    if event_type in ("pricing_review", "refund_request"):
        return "approval_action"
    return "general_request"


def plan_delegation(operation_type: str, event_type: str) -> list[str]:
    """Which specialists are needed. Independent tasks => run in parallel."""
    if operation_type == "inventory_analysis":
        return ["business_analyst", "knowledge_agent", "operations_agent"]
    if operation_type == "daily_report":
        return ["business_analyst", "report_agent"]
    if operation_type == "approval_action":
        return ["knowledge_agent", "operations_agent"]
    if operation_type == "smalltalk":
        return []
    return ["business_analyst", "knowledge_agent"]


_RUNNERS = {
    "business_analyst": business_analyst.run,
    "knowledge_agent": knowledge_agent.run,
    "operations_agent": operations_agent.run,
    "report_agent": report_agent.run,
}


async def run_specialists(names: list[str], event_type: str, entity: str,
                          payload: dict, correlation_id: str) -> list[AgentResult]:
    """REAL concurrency: asyncio.gather over independent specialist coroutines."""
    coros = [_RUNNERS[n](event_type, entity, payload, correlation_id) for n in names if n in _RUNNERS]
    if not coros:
        return []
    results = await asyncio.gather(*coros)
    for r in results:
        observe(r.agent, r.duration_ms)
    return list(results)


async def run_specialists_sequential(names: list[str], event_type: str, entity: str,
                                     payload: dict, correlation_id: str) -> list[AgentResult]:
    """Sequential baseline (for benchmark comparison only)."""
    out: list[AgentResult] = []
    for n in names:
        if n in _RUNNERS:
            out.append(await _RUNNERS[n](event_type, entity, payload, correlation_id))
    return out


def aggregate(operation_type: str, results: list[AgentResult]) -> dict:
    by = {r.agent: r for r in results}
    analyst = by.get("business_analyst")
    knowledge = by.get("knowledge_agent")
    ops = by.get("operations_agent")
    confidences = [r.confidence for r in results if r.status == "completed"]
    confidence = sum(confidences) / len(confidences) if confidences else 0.5
    evidence = []
    for r in results:
        for e in r.evidence:
            evidence.append({"source": f"{r.agent}:{e.get('source', '')}",
                             "content": str(e.get("content", ""))[:300],
                             "score": float(e.get("score", 0.0))})
    needs_more = any(r.requires_more_context for r in results)
    return {"by": by, "analyst": analyst, "knowledge": knowledge, "ops": ops,
            "confidence": confidence, "evidence": evidence, "needs_more_context": needs_more}


async def supervise(correlation_id: str, event_type: str, entity: str,
                    payload: dict, context: dict | None = None) -> dict:
    t0 = time.perf_counter()
    decision_id = "dec_" + uuid.uuid4().hex[:8]
    operation_type = classify_operation(event_type, payload)
    context = context or {}

    # Bounded memory: last 5 entries for this chat/entity (never full history).
    mkey = memory_key(event_type, entity, payload)
    history = recall(mkey, limit=5)

    # Smalltalk fast-path: no specialists needed.
    if operation_type == "smalltalk":
        out = await reason(event_type, entity, payload)
        latency = int((time.perf_counter() - t0) * 1000)
        gate = check("NOTIFY", "", "general")
        remember(mkey, {"correlation_id": correlation_id, "operation_type": operation_type})
        return {"decision_id": decision_id, "correlation_id": correlation_id,
                "operation_type": operation_type, "action_type": "NOTIFY",
                "recommended_action": "", "needs_approval": gate.needs_approval,
                "policy_matched": gate.policy_matched, "summary": out.get("summary", ""),
                "reasoning": out.get("reasoning", ""), "confidence": float(out.get("confidence", 0.7)),
                "priority": "LOW", "specialists_used": [], "evidence": [],
                "ticket_type": "general", "failure_kind": "SUCCESS",
                "action": {"type": "send_message", "parameters": {}},
                "supervisor_latency_ms": latency, "specialist_results": []}

    names = plan_delegation(operation_type, event_type)
    results = await run_specialists(names, event_type, entity, payload, correlation_id)
    agg = aggregate(operation_type, results)

    # Report agent runs AFTER first wave (needs specialist outputs to summarize).
    report_res: AgentResult | None = None
    if "report_agent" in names:
        report_res = next((r for r in results if r.agent == "report_agent"), None)
        # enrich report with sibling outputs
        if report_res is not None:
            enriched = await report_agent.run(event_type, entity, payload, correlation_id,
                                              specialist_results=[r for r in results if r.agent != "report_agent"])
            results = [r for r in results if r.agent != "report_agent"] + [enriched]
            agg = aggregate(operation_type, results)

    # LLM interpretation layer (mock-by-default, Cloudflare when configured).
    llm = await reason(event_type, entity, payload)

    # Decide action_type: deterministic signals first, LLM as fallback.
    analyst = agg["analyst"]
    text = str(payload.get("text", "")).lower()
    action_type = llm.get("action_type", "REPORT")
    recommended = llm.get("recommended_action", "")
    ticket_type = llm.get("ticket_type", "general")
    if analyst and "risk_level=HIGH" in " ".join(analyst.findings):
        action_type, recommended, ticket_type = "TICKET", "restock_200", "inventory_risk"
    if operation_type == "daily_report":
        action_type, recommended, ticket_type = "REPORT", "", "general"
    if any(k in text for k in ("delete", "drop", "destroy", "shutdown")):
        action_type = "EXECUTE"  # policy gate will DENY below
        recommended = "delete_system_data"

    gate = check(action_type, recommended, ticket_type)
    failure_kind = "SUCCESS"
    needs_approval = gate.needs_approval
    if action_type == "EXECUTE" and "delete" in recommended:
        failure_kind = "DENIED"
    elif needs_approval:
        failure_kind = "APPROVAL_REQUIRED"

    priority = "HIGH" if (analyst and "HIGH" in " ".join(analyst.findings)) else "MEDIUM"
    if operation_type == "daily_report":
        priority = "LOW"

    summary = llm.get("summary", "")
    if payload.get("source") == "telegram" and llm.get("reply_text"):
        summary = llm["reply_text"]
    if analyst and operation_type == "inventory_analysis":
        summary = analyst.summary

    action_map = {"REPORT": "append_report_row", "NOTIFY": "send_message",
                  "TICKET": "create_ticket", "EXECUTE": "execute_action"}
    latency = int((time.perf_counter() - t0) * 1000)
    observe("supervisor", latency)
    remember(mkey, {"correlation_id": correlation_id, "operation_type": operation_type,
                    "action_type": action_type})
    return {
        "decision_id": decision_id, "correlation_id": correlation_id,
        "operation_type": operation_type, "action_type": action_type,
        "recommended_action": recommended, "needs_approval": needs_approval,
        "requires_approval": needs_approval, "policy_matched": gate.policy_matched,
        "summary": summary, "reasoning": llm.get("reasoning", ""),
        "confidence": round(min(agg["confidence"], 0.99), 3) if results else float(llm.get("confidence", 0.5)),
        "priority": priority, "specialists_used": [r.agent for r in results],
        "evidence": agg["evidence"], "ticket_type": ticket_type,
        "failure_kind": failure_kind,
        "action": {"type": action_map.get(action_type, "send_message"),
                   "parameters": {"entity": entity, "recommended_action": recommended}},
        "supervisor_latency_ms": latency, "specialist_results": results,
        "history_used": len(history),
    }
