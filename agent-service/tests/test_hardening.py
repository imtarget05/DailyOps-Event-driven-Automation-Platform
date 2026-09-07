"""Policy engine hardening: token-boundary deny, yaml thresholds, idempotency key."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from src.guardrails import policy_engine as pe
from src.main import app

client = TestClient(app)


def test_deny_token_boundary():
    # destructive verbs matched as tokens
    assert pe.is_destructive("delete_system_data") is True
    assert pe.is_destructive("drop_table") is True
    # benign words containing deny substrings must NOT be denied
    assert pe.is_destructive("append_deadline_row") is False
    assert pe.is_destructive("send_email_flyer") is False


def test_threshold_from_yaml():
    d = pe.check("TICKET", "restock_400", "inventory_risk")
    assert d.verdict == "ALLOW"
    d = pe.check("TICKET", "restock_600", "inventory_risk")
    assert d.verdict == "APPROVAL"
    assert d.policy_matched == "inventory_policy.restock_approval_threshold"


def test_custom_threshold_file(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("thresholds:\n  restock_auto_max: 100\npolicies: []\n", encoding="utf-8")
    d = pe.check("TICKET", "restock_200", "inventory_risk", path=str(p))
    assert d.verdict == "APPROVAL"


def test_idempotency_key_stable_and_unique():
    payload = {"correlation_id": "run_idem_1",
               "event": {"type": "inventory_check", "entity": "product_A",
                         "payload": {"stock": 15, "avg_daily_sales": 8}}}
    a = client.post("/agent/decide", json=payload).json()["idempotency_key"]
    b = client.post("/agent/decide", json=payload).json()["idempotency_key"]
    assert a and a == b  # same action => same key (safe for retries)
    other = client.post("/agent/decide", json={
        "correlation_id": "run_idem_2",
        "event": {"type": "inventory_check", "entity": "product_B",
                  "payload": {"stock": 15, "avg_daily_sales": 8}}}).json()["idempotency_key"]
    assert other != a  # different correlation => different key
