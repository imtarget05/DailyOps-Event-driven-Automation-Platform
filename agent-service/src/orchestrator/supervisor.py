"""DailyOps Supervisor: classify -> delegate (asyncio.gather) -> aggregate -> decision.

Supervisor NEVER performs side effects. It only plans delegation, runs
specialist agents concurrently, aggregates structured AgentResults, and
produces a canonical decision consumed by n8n + Policy Engine.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid

from ..agents import advisory_council, business_analyst, finance_agent, knowledge_agent, operations_agent, report_agent
from ..agents.base import AgentResult
from ..guardrails.policy_engine import PolicyDecision, _enforce_auth_claims, check
from ..memory.store import memory_key, recall, remember
from ..observability.metrics import observe
from ..reasoning.llm_client import reason

ANALYST_EVENTS = {"inventory_check", "business_event", "daily_report", "sales_review"}
ALL_SPECIALISTS = ("business_analyst", "knowledge_agent", "operations_agent", "report_agent",
                   "advisory_council", "finance_agent")

# Per-specialist execution budget. A hung specialist must degrade to a `failed`
# AgentResult, never block the whole wave. Overridable via env for tuning.
SPECIALIST_TIMEOUT_SECONDS = float(os.environ.get("AGENT_SPECIALIST_TIMEOUT_S", "10.0"))

ADVISORY_KEYWORDS = (
    "tư vấn", "tu van", "advisory", "cố vấn", "co van", "hội đồng", "hoi dong",
    "chiến lược", "chien luoc", "strategy", "hormozi", "buffett", "gary",
)


def classify_operation(event_type: str, payload: dict) -> str:
    text = str(payload.get("text", "")).lower()
    if event_type in ("advisory_request", "business_strategy", "investment", "marketing"):
        return "advisory_request"
    if event_type == "inventory_check" or any(k in text for k in ("tồn kho", "hết hàng", "restock", "kho")):
        return "inventory_analysis"
    if event_type == "telegram_chat":
        if any(k in text for k in ("báo cáo", "report", "tổng hợp", "thống kê")):
            return "daily_report"
        if any(k in text for k in ("hoàn tiền", "refund", "đổi giá", "giá")):
            return "approval_action"
        if any(k in text for k in ADVISORY_KEYWORDS):
            return "advisory_request"
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
    if operation_type == "advisory_request":
        # Knowledge first for grounding, advisory cites its evidence.
        return ["knowledge_agent", "advisory_council"]
    if operation_type == "inventory_analysis":
        return ["business_analyst", "knowledge_agent", "operations_agent"]
    if operation_type == "daily_report":
        return ["business_analyst", "report_agent"]
    if operation_type == "approval_action":
        if event_type == "refund_request":
            return ["finance_agent", "knowledge_agent", "operations_agent"]
        return ["knowledge_agent", "operations_agent"]
    if operation_type == "smalltalk":
        return []
    return ["business_analyst", "knowledge_agent"]


_RUNNERS = {
    "business_analyst": business_analyst.run,
    "knowledge_agent": knowledge_agent.run,
    "operations_agent": operations_agent.run,
    "report_agent": report_agent.run,
    "advisory_council": advisory_council.run,
    "finance_agent": finance_agent.run,
}


async def _safely_run_specialist(name: str, runner, event_type: str, entity: str,
                                 payload: dict, correlation_id: str) -> AgentResult:
    """Run one specialist under a budget; never raise.

    A crashing or hung specialist degrades to a `failed` AgentResult with an
    explicit summary instead of aborting the whole wave and discarding every
    sibling's partial result (DAILYOPS-OPS-5: failure isolation).
    """
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            runner(event_type, entity, payload, correlation_id),
            timeout=SPECIALIST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return AgentResult(agent=name, status="failed",
                           summary=f"specialist timed out after {int(SPECIALIST_TIMEOUT_SECONDS * 1000)}ms",
                           confidence=0.0, duration_ms=int(SPECIALIST_TIMEOUT_SECONDS * 1000))
    except Exception as exc:  # isolate any specialist crash
        return AgentResult(agent=name, status="failed",
                           summary=f"specialist failed: {type(exc).__name__}: {str(exc)[:120]}",
                           confidence=0.0, duration_ms=int((time.perf_counter() - t0) * 1000))
    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    return result


async def run_specialists(names: list[str], event_type: str, entity: str,
                          payload: dict, correlation_id: str) -> list[AgentResult]:
    """REAL concurrency: asyncio.gather over independent specialist coroutines.

    Each specialist runs under SPECIALIST_TIMEOUT_SECONDS and is isolated, so
    one failure can never abort the wave or block sibling specialists.
    """
    coros = [
        _safely_run_specialist(n, _RUNNERS[n], event_type, entity, payload, correlation_id)
        for n in names if n in _RUNNERS
    ]
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
    try:
        results = await run_specialists(names, event_type, entity, payload, correlation_id)
    except Exception as exc:  # DAILYOPS-10: agent failure must not 500 the decide API.
        latency = int((time.perf_counter() - t0) * 1000)
        observe("supervisor_error", latency)
        return {"decision_id": decision_id, "correlation_id": correlation_id,
                "operation_type": operation_type, "action_type": "UNKNOWN",
                "recommended_action": "", "needs_approval": False,
                "policy_matched": "agent_failure_fallback", "summary": "",
                "reasoning": f"agent failure: {type(exc).__name__}",
                "confidence": 0.0, "priority": "HIGH", "specialists_used": [],
                "evidence": [], "ticket_type": "general", "failure_kind": "FAILED",
                "action": {"type": "send_message", "parameters": {}},
                "supervisor_latency_ms": latency, "specialist_results": [],
                "history_used": len(history)}
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
    # DAILYOPS-11: enforcement-aware authorization claim detection.
    # Llm output is run through _enforce_auth_claims which returns both a
    # cleaned proposal and a classification (NONE / SUSPICIOUS / PRIVILEGE_ESC).
    # Classification guides the policy gate below.
    llm, auth_classification = _enforce_auth_claims(await reason(event_type, entity, payload))

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
    # Refund requests: deterministic EXECUTE proposal so the policy gate
    # (refund_large_approval) decides approval — LLM never self-authorizes.
    if operation_type == "approval_action" and event_type == "refund_request":
        amount = payload.get("refund_amount") or payload.get("amount") or ""
        action_type, recommended, ticket_type = "EXECUTE", f"refund_{amount}", "finance_refund"
    # Advisory council: REPORT/NOTIFY only, grounded by Phase-2 RAG.
    # Never TICKET/EXECUTE even if LLM proposes otherwise.
    if operation_type == "advisory_request":
        action_type, recommended, ticket_type = "REPORT", "advisory_brief", "advisory"
        if payload.get("source") == "telegram":
            action_type = "NOTIFY"
    if any(k in text for k in ("delete", "drop", "destroy", "shutdown")):
        action_type = "EXECUTE"  # policy gate will DENY below
        recommended = "delete_system_data"

    gate = check(action_type, recommended, ticket_type)
    failure_kind = "SUCCESS"
    needs_approval = gate.needs_approval
    # DAILYOPS-11: enforcement-aware auth classification.
    # If semantic claims were detected, override the gate to fail closed:
    #   PRIVILEGE_ESC → hard deny (the LLM is attempting privileged escalation)
    #   SUSPICIOUS  → approval required even for normally-auto actions
    #   NONE        → proceed with normal gate outcome
    if auth_classification == "PRIVILEGE_ESC":
        failure_kind = "DENIED"
        needs_approval = True
        gate = PolicyDecision(True, "semantic_privilege_escalation_attempt", "DENY")
    elif auth_classification == "SUSPICIOUS":
        # For normally-auto actions (REPORT/NOTIFY/TICKET with small qty),
        # still require approval when claims are suspicious.
        if action_type in ("REPORT", "NOTIFY", "TICKET"):
            needs_approval = True
            if failure_kind == "SUCCESS":
                failure_kind = "APPROVAL_REQUIRED"
    # NONE: no special handling needed; proceed with normal gate outcome
    elif auth_classification == "NONE":
        pass  # already handled by the gate above
    if getattr(gate, "verdict", "") == "DENY":
        failure_kind = "DENIED"
    elif action_type == "EXECUTE" and "delete" in recommended:
        failure_kind = "DENIED"
    elif needs_approval:
        failure_kind = "APPROVAL_REQUIRED"

    priority = "HIGH" if (analyst and "HIGH" in " ".join(analyst.findings)) else "MEDIUM"
    if operation_type == "daily_report":
        priority = "LOW"
    if operation_type == "advisory_request":
        priority = "LOW"

    summary = llm.get("summary", "")
    if payload.get("source") == "telegram" and llm.get("reply_text"):
        summary = llm["reply_text"]
    if analyst and operation_type == "inventory_analysis":
        summary = analyst.summary
    if operation_type == "advisory_request":
        advisory_res = agg["by"].get("advisory_council") if isinstance(agg.get("by"), dict) else None
        if advisory_res is not None and getattr(advisory_res, "summary", ""):
            summary = advisory_res.summary

    action_map = {"REPORT": "append_report_row", "NOTIFY": "send_message",
                  "TICKET": "create_ticket", "EXECUTE": "execute_action"}
    latency = int((time.perf_counter() - t0) * 1000)
    observe("supervisor", latency)
    remember(mkey, {"correlation_id": correlation_id, "operation_type": operation_type,
                    "action_type": action_type})
    # Idempotency key: n8n retries / webhook replays of the same
    # (correlation_id, action_type, recommended_action) collapse onto one row.
    idem = hashlib.sha256(f"{correlation_id}:{action_type}:{recommended}".encode()).hexdigest()[:16]
    return {
        "decision_id": decision_id, "correlation_id": correlation_id,
        "idempotency_key": idem,
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
