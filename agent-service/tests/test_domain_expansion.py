"""Domain expansion tests: knowledge-base RAG wiring, new specialist agent,
new domain policies (CRM/Marketing/HR/Finance). Behavior contracts only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.guardrails.policy_engine import check
from src.rag.knowledge_base import ensure_indexed
from src.rag.ingestion import get_store


# ------------------------------------------------ knowledge base (real RAG)

def test_knowledge_base_indexes_sop_documents():
    ensure_indexed()  # idempotent: may be a no-op if another test already indexed
    assert len(get_store()) > 0, "SOP documents must be present in the vector store"


def test_knowledge_base_is_idempotent():
    before = len(get_store())
    ensure_indexed()
    ensure_indexed()  # no mtime change -> no re-ingest
    assert len(get_store()) == before


def test_knowledge_agent_grounds_from_ingested_sops():
    import asyncio
    from src.agents.knowledge_agent import run

    result = asyncio.run(run("refund_request", "order_9",
                             {"text": "hoàn tiền đơn hàng 1500000", "refund_amount": 1500},
                             "corr_kb_1"))
    sources = {e["source"] for e in result.evidence}
    assert "policy_rag" in sources
    # SOP docs exist and are indexed -> knowledge_base evidence present
    if len(get_store()) > 0:
        assert "knowledge_base" in sources


# ------------------------------------------------ finance agent (new specialist)

def test_finance_agent_classifies_high_risk_refund():
    import asyncio
    from src.agents.finance_agent import run

    result = asyncio.run(run("refund_request", "cust_big",
                             {"refund_amount": 1500}, "corr_fin_1"))
    assert result.status == "completed"
    assert "risk_level=HIGH" in " ".join(result.findings)
    assert result.recommendations == ["refund_request_approval"]


def test_finance_agent_low_risk_note():
    import asyncio
    from src.agents.finance_agent import run

    # 20 < MEDIUM threshold (20% of yaml finance_refund_auto_max=200)
    result = asyncio.run(run("refund_request", "cust_small",
                             {"refund_amount": 20}, "corr_fin_2"))
    assert "risk_level=LOW" in " ".join(result.findings)


def test_refund_request_delegates_to_finance_agent():
    from fastapi.testclient import TestClient
    from src.main import app

    body = TestClient(app).post("/agent/decide", json={
        "correlation_id": "run_fin_1",
        "event": {"type": "refund_request", "entity": "cust_big",
                  "payload": {"refund_amount": 1500}},
    }).json()
    assert "finance_agent" in body["specialists_used"]
    assert body["needs_approval"] is True


# ------------------------------------------------ domain policies

def test_crm_bulk_update_needs_approval():
    d = check("EXECUTE", "crm_bulk_update_segment", "crm_bulk_update")
    assert d.verdict == "APPROVAL"
    assert d.policy_matched == "crm_bulk_update_approval"


def test_marketing_spend_needs_approval_but_report_is_auto():
    d = check("EXECUTE", "increase_budget_500", "marketing_spend")
    assert d.verdict == "APPROVAL"
    d = check("REPORT", "campaign_summary", "marketing_spend")
    assert d.verdict == "ALLOW"


def test_hr_offboarding_needs_approval():
    d = check("EXECUTE", "offboard_employee", "hr_offboarding")
    assert d.verdict == "APPROVAL"
    assert d.policy_matched == "hr_offboarding_approval"


def test_yaml_domain_policies_are_loadable():
    from src.guardrails.policy_engine import _load_policies

    names = {p["name"] for p in _load_policies()}
    for expected in ("crm_bulk_update_approval", "marketing_budget_spend_approval",
                     "hr_offboarding_approval", "finance_refund_approval"):
        assert expected in names


def test_finance_thresholds_come_from_yaml_not_hardcode():
    from src.guardrails.policy_engine import _load_thresholds

    thr = _load_thresholds()
    assert thr["finance_refund_auto_max"] == 200
    # TICKET with small refund amount -> auto note (yaml threshold decides)
    d = check("TICKET", "refund_150", "finance_refund")
    assert d.verdict == "ALLOW" and d.policy_matched == "finance_refund_auto_note"
    # EXECUTE with amount above yaml threshold -> finance approval policy
    d = check("EXECUTE", "refund_1500", "finance_refund")
    assert d.verdict == "APPROVAL" and d.policy_matched == "finance_refund_approval"


def test_custom_finance_threshold_file(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("thresholds:\n  restock_auto_max: 500\n  finance_refund_auto_max: 50\n"
                 "policies: []\n", encoding="utf-8")
    d = check("TICKET", "refund_80", "finance_refund", path=str(p))
    assert d.verdict == "APPROVAL"  # 80 > custom 50


def test_existing_policies_unchanged_regression():
    assert check("TICKET", "restock_200", "inventory_risk").verdict == "ALLOW"
    assert check("EXECUTE", "restock_600", "inventory_risk").verdict == "APPROVAL"
    assert check("EXECUTE", "delete_system_data").verdict == "DENY"
