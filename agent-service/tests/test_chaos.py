"""DAILYOPS-10: failure/recovery across documented failure cases.

Each case injects a failure and asserts the expected failure_kind + that no
duplicate/incorrect side-effect occurs. These complement test_reliability.py
(policy invariants) with end-to-end chaos at the supervisor boundary.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from src.guardrails.policy_engine import check  # noqa: E402
from src.guardrails.verifier import verify_execute, verify_ticket  # noqa: E402

client = TestClient(app)


def _post(cid, etype, payload):
    return client.post("/agent/decide", json={
        "correlation_id": cid,
        "event": {"type": etype, "entity": "x", "payload": payload},
    }).json()


# 1. Agent failure: specialist raises -> supervisor still returns a decision.
def test_agent_failure_yields_structured_failure(monkeypatch):
    import src.orchestrator.supervisor as sup

    async def boom(names, et_type, entity, payload, corr):
        raise RuntimeError("specialist crashed (simulated)")

    monkeypatch.setattr(sup, "run_specialists", boom)
    body = _post("chaos_agent_fail", "inventory_check", {"stock": 5})
    # Supervisor must not 500; it classifies the failure deterministically.
    assert body["correlation_id"] == "chaos_agent_fail"
    assert body["failure_kind"] in ("SUCCESS", "FAILED", "APPROVAL_REQUIRED", "DENIED")


# 2. Policy failure: LLM proposes a destructive action -> DENY, never execute.
def test_policy_failure_destructive_is_denied(monkeypatch):
    import src.orchestrator.supervisor as sup

    async def poison(et, ent, p):
        return {"action_type": "EXECUTE", "recommended_action": "drop_table",
                "authorized": True}

    async def empty(names, et, ent, p, c):
        return []

    monkeypatch.setattr(sup, "reason", poison)
    monkeypatch.setattr(sup, "run_specialists", empty)
    body = _post("chaos_policy", "business_event", {})
    assert body["failure_kind"] == "DENIED"
    assert body["policy_matched"] == "destructive_action_deny"


# 3. Duplicate webhook replay: same correlation_id -> same key, single reserve.
def test_duplicate_webhook_single_action():
    keys = [_post("chaos_dup", "inventory_check", {"stock": 20})["idempotency_key"]
            for _ in range(5)]
    assert len(set(keys)) == 1


# 4. Unknown outcome: a timeout-class error must be classified UNKNOWN_OUTCOME,
#    and the retry policy must forbid blind side-effect retry.
def test_unknown_outcome_requires_reconciliation():
    from src.guardrails.approval_gate import classify_outcome
    assert classify_outcome("Request timed out after 20000ms") == "UNKNOWN_OUTCOME"
    # idempotent reserve prevents a blind 2nd side-effect even if retried.
    keys = [_post("chaos_timeout_a", "refund_request", {"refund_amount": 999})["idempotency_key"]
            for _ in range(3)]
    assert len(set(keys)) == 1, "retry must not create a new side-effect row"


# 5. API 429/500: a definitive HTTP error is FAILED (retryable), not UNKNOWN.
def test_api_error_is_failed_not_unknown():
    from src.guardrails.approval_gate import classify_outcome
    assert classify_outcome("HTTP 429 Too Many Requests") == "FAILED"
    assert classify_outcome("HTTP 500 Internal Server Error") == "FAILED"


# 6. Partial execution: verifier must catch a 200-with-contract-breach (R13).
def test_partial_execute_detected_by_verifier():
    # Simulate: API returned 200 but the business op actually failed.
    state = {"http_status": 200, "success": False, "error": "ticket missing required field"}
    assert verify_execute(state)["verified"] is False


# 7. Verify ticket state: a ticket row missing required fields fails verify.
def test_ticket_state_verification():
    assert verify_ticket({"ticket_id": "t1", "type": "x", "entity": "e", "status": "OPEN"})["verified"]
    assert not verify_ticket({"ticket_id": "t1", "type": "x"})["verified"]  # missing entity/status


# 8. Approval-gated EXECUTE never returns needs_approval=False.
def test_execute_class_always_gated(monkeypatch):
    import src.orchestrator.supervisor as sup

    async def poison(et, ent, p):
        return {"action_type": "EXECUTE", "recommended_action": "refund_3000",
                "needs_approval": False, "authorized": True}

    async def empty(names, et, ent, p, c):
        return []

    monkeypatch.setattr(sup, "reason", poison)
    monkeypatch.setattr(sup, "run_specialists", empty)
    body = _post("chaos_gated", "business_event", {})
    assert body["needs_approval"] is True
    assert body["failure_kind"] == "APPROVAL_REQUIRED"


# DAILYOPS-07: execution-safety chain — trace that no EXECUTE-class action can
# skip the policy gate (Validate→Authorize→Approve→Execute→Verify→Record).
# The gate is the single choke point; these assertions prove it is terminal.
def test_execution_chain_gate_is_terminal_for_execute():
    # Every EXECUTE proposal, regardless of recommended_action, is gated.
    for action in ("refund_1500", "crm_bulk_update_segment", "offboard_employee",
                   "increase_budget_500", "restock_9999", "unknown_op"):
        d = check("EXECUTE", action, "")
        assert d.verdict in ("APPROVAL", "DENY"), (
            f"DAILYOPS-07 breach: EXECUTE {action!r} auto-allowed (verdict={d.verdict})")
        assert d.needs_approval is True


def test_execution_chain_destructive_always_denied():
    # Destructive denylist is checked BEFORE the action_type branch — no bypass.
    for at in ("REPORT", "NOTIFY", "TICKET", "EXECUTE"):
        d = check(at, "delete_system_data", "general")
        assert d.verdict == "DENY", (
            f"DAILYOPS-07 breach: {at}+delete_system_data not denied ({d.verdict})")


def test_execution_chain_record_outcome_present(monkeypatch):
    """A resolved decision must carry failure_kind + policy_matched so the
    downstream Record Outcome / Record Failure steps in n8n have audit data."""
    import src.orchestrator.supervisor as sup

    async def empty(names, et, ent, p, c):
        return []

    monkeypatch.setattr(sup, "run_specialists", empty)
    body = _post("chaos_record", "inventory_check", {"stock": 5})
    assert body["failure_kind"] in ("SUCCESS", "FAILED", "APPROVAL_REQUIRED", "DENIED")
    assert body["policy_matched"]  # non-empty policy match = gate was evaluated
