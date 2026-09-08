"""Multi-agent orchestrator tests: delegation, contracts, parallel, policy, failure."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from src.agents.base import AgentResult
from src.main import app
from src.orchestrator import supervisor as sup

client = TestClient(app)


def _decide(cid: str, etype: str, entity: str, payload: dict) -> dict:
    res = client.post("/agent/decide", json={
        "correlation_id": cid,
        "event": {"type": etype, "entity": entity, "payload": payload},
    })
    assert res.status_code == 200, res.text
    return res.json()


# CASE 1 — daily report -> REPORT
def test_case1_daily_report():
    body = _decide("run_case1", "daily_report", "shop",
                   {"stock": 100, "avg_daily_sales": 8})
    assert body["action_type"] == "REPORT"
    assert body["operation_type"] == "daily_report"
    assert "report_agent" in body["specialists_used"]
    assert body["failure_kind"] == "SUCCESS"


# CASE 2 — inventory risk -> analyst + knowledge -> TICKET
def test_case2_inventory_risk_ticket():
    body = _decide("run_case2", "inventory_check", "product_A",
                   {"stock": 15, "avg_daily_sales": 8})
    assert body["action_type"] == "TICKET"
    assert "business_analyst" in body["specialists_used"]
    assert "knowledge_agent" in body["specialists_used"]
    assert body["needs_approval"] is False
    assert body["decision_id"].startswith("dec_")
    assert len(body["evidence"]) >= 2


# CASE 3 — safe autonomous action (low restock ticket, auto ALLOW)
def test_case3_safe_auto_allow():
    body = _decide("run_case3", "inventory_check", "product_A",
                   {"stock": 15, "avg_daily_sales": 8})
    assert body["needs_approval"] is False
    assert body["failure_kind"] == "SUCCESS"


# CASE 4 — approval-required action blocks execution (no auto-execute)
def test_case4_approval_required():
    body = _decide("run_case4", "telegram_chat", "98765",
                   {"text": "cho mình hoàn tiền đơn hàng nhé", "chat_id": 98765,
                    "source": "telegram"})
    assert body["action_type"] == "EXECUTE"
    assert body["needs_approval"] is True
    assert body["requires_approval"] is True
    assert body["failure_kind"] == "APPROVAL_REQUIRED"


# CASE 5 — denied action -> no side effect
def test_case5_denied_action():
    body = _decide("run_case5", "telegram_chat", "98765",
                   {"text": "delete all system data now", "chat_id": 98765,
                    "source": "telegram"})
    assert body["failure_kind"] == "DENIED"
    assert body["policy_matched"] == "destructive_action_deny"


# CASE 6 — agent failure -> structured failure classification
def test_case6_structured_agent_contract():
    res = client.post("/agent/specialist/business_analyst", json={
        "correlation_id": "run_case6",
        "event": {"type": "inventory_check", "entity": "product_A",
                  "payload": {"stock": "NaN-broken", "avg_daily_sales": 8}},
    })
    assert res.status_code == 200
    body = res.json()
    assert set(body) >= {"agent", "task_id", "status", "summary", "findings",
                         "evidence", "recommendations", "confidence",
                         "requires_more_context"}
    assert body["agent"] == "business_analyst"


# CASE 7 — parallel specialists actually run concurrently
def test_case7_parallel_execution():
    async def go():
        names = ["business_analyst", "knowledge_agent", "operations_agent"]
        t0 = time.perf_counter()
        await sup.run_specialists(names, "inventory_check", "product_A",
                                  {"stock": 15, "avg_daily_sales": 8}, "run_case7")
        t_par = time.perf_counter() - t0
        t0 = time.perf_counter()
        await sup.run_specialists_sequential(names, "inventory_check", "product_A",
                                             {"stock": 15, "avg_daily_sales": 8}, "run_case7")
        t_seq = time.perf_counter() - t0
        return t_par, t_seq
    t_par, t_seq = asyncio.run(go())
    assert t_par < t_seq, f"parallel {t_par:.3f}s should beat sequential {t_seq:.3f}s"


# CASE 7b — a crashing specialist degrades to `failed`, never aborts the wave
def test_case_crashing_specialist_is_isolated(monkeypatch):
    async def boom(event_type, entity, payload, correlation_id):
        raise RuntimeError("kaboom")
    async def fine(event_type, entity, payload, correlation_id):
        return AgentResult(agent="fine_specialist", status="completed",
                           summary="ok", confidence=0.7)
    monkeypatch.setattr(sup, "_RUNNERS", {"boom": boom, "fine_specialist": fine})
    results = asyncio.run(sup.run_specialists(
        ["boom", "fine_specialist"], "t", "e", {}, "cid_iso"))
    by = {r.agent: r for r in results}
    assert by["boom"].status == "failed"
    assert "kaboom" in by["boom"].summary
    assert by["fine_specialist"].status == "completed", \
        "sibling specialist must still produce a result"


# CASE 7c — a hung specialist times out, sibling results are not lost
def test_case_slow_specialist_times_out(monkeypatch):
    async def hang(event_type, entity, payload, correlation_id):
        await asyncio.sleep(60)
        return AgentResult(agent="hang", status="completed", summary="late")
    async def quick(event_type, entity, payload, correlation_id):
        return AgentResult(agent="quick", status="completed",
                           summary="done", confidence=0.8)
    monkeypatch.setattr(sup, "SPECIALIST_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(sup, "_RUNNERS", {"hang": hang, "quick": quick})
    results = asyncio.run(sup.run_specialists(
        ["hang", "quick"], "t", "e", {}, "cid_timeout"))
    by = {r.agent: r for r in results}
    assert by["hang"].status == "failed"
    assert "timed out" in by["hang"].summary
    assert by["quick"].status == "completed"


# CASE 8 — telegram request -> context -> supervisor -> decision -> reply
def test_case8_telegram_stock_reply():
    body = _decide("run_case8", "telegram_chat", "98765",
                   {"text": "kho còn bao nhiêu, hết hàng chưa?", "chat_id": 98765,
                    "source": "telegram"})
    assert body["action_type"] == "TICKET"
    assert body["operation_type"] == "inventory_analysis"
    assert body["decision"]["summary"], "reply text must be non-empty"


# CASE 9 — ticket payload present + knowledge evidence cited
def test_case9_ticket_with_evidence():
    body = _decide("run_case9", "inventory_check", "product_A",
                   {"stock": 15, "avg_daily_sales": 8})
    assert body["ticket_payload"] is not None
    assert body["ticket_payload"]["type"] == "inventory_risk"
    assert any("policy" in e["source"] for e in body["evidence"])
