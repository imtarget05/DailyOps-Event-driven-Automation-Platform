"""Advisory Council grounding checks: personas must cite RAG, low grounding -> needs_context."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from src.agents import advisory_council
from src.guardrails import policy_engine as pe
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


def test_grounding_score_high_when_cited():
    snippets = ["advisory_policy.business_grounding: tu van kinh doanh phai dua tren SOP noi bo"]
    advice = "Goc nhin kinh doanh dua tren SOP noi bo, doi chieu SOP truoc khi hanh dong [nguon: advisory_policy.business_grounding]"
    assert advisory_council.grounding_score(advice, snippets) >= advisory_council.GROUNDING_THRESHOLD


def test_grounding_score_low_when_hallucinated():
    snippets = ["default: REPORT/NOTIFY/TICKET auto, EXECUTE needs approval"]
    advice = "Crypto moon 100x chac chan, all-in dogecoin khong can stop-loss"
    assert advisory_council.grounding_score(advice, snippets) < advisory_council.GROUNDING_THRESHOLD


def test_advisory_grounded_completes():
    import asyncio
    res = asyncio.run(advisory_council.run(
        "advisory_request", "shop",
        {"text": "tu van chien luoc kinh doanh quy toi"}, "t1"))
    assert res.agent == "advisory_council"
    assert res.status == "completed"
    assert res.requires_more_context is False
    assert res.confidence >= 0.6
    assert len(res.findings) == 3  # 3 personas
    assert any("policy_rag" in str(e.get("source", "")) for e in res.evidence)


def test_advisory_ungrounded_needs_context():
    import asyncio
    res = asyncio.run(advisory_council.run(
        "advisory_request", "shop",
        {"text": "crypto moon 100x all-in dogecoin xyzzy-plugh"}, "t2"))
    assert res.status == "needs_context"
    assert res.requires_more_context is True
    assert res.confidence <= 0.4
    assert "Chua du du lieu" in res.summary


def test_supervisor_advisory_never_executes():
    body = _decide("run_adv_1", "advisory_request", "shop",
                   {"text": "tu van chien luoc kinh doanh"})
    assert body["operation_type"] == "advisory_request"
    assert body["action_type"] in ("REPORT", "NOTIFY")
    assert body["action_type"] not in ("TICKET", "EXECUTE")
    assert "advisory_council" in body["specialists_used"]
    assert body["needs_approval"] is False
    assert body["failure_kind"] == "SUCCESS"


def test_supervisor_advisory_telegram_notifies():
    body = _decide("run_adv_2", "telegram_chat", "98765",
                   {"text": "tu van chien luoc kinh doanh cho shop", "chat_id": 98765,
                    "source": "telegram"})
    assert body["operation_type"] == "advisory_request"
    assert body["action_type"] == "NOTIFY"


def test_policy_denies_advisory_execute():
    d = pe.check("EXECUTE", "advisory_brief", "advisory")
    assert d.verdict == "DENY"
    assert d.policy_matched == "advisory_report_only"
    d2 = pe.check("REPORT", "advisory_brief", "advisory")
    assert d2.verdict == "ALLOW"


def test_specialist_endpoint_exposes_advisory():
    res = client.post("/agent/specialist/advisory_council", json={
        "correlation_id": "run_adv_3",
        "event": {"type": "advisory_request", "entity": "shop",
                  "payload": {"text": "tu van dau tu dai han"}},
    })
    assert res.status_code == 200
    body = res.json()
    assert body["agent"] == "advisory_council"
    assert set(body) >= {"agent", "task_id", "status", "summary", "findings",
                         "evidence", "recommendations", "confidence",
                         "requires_more_context"}
