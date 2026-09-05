import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_decide_inventory_risk():
    res = client.post("/agent/decide", json={
        "correlation_id": "run_20260905_0800_001",
        "event": {"type": "inventory_check", "entity": "product_A",
                  "payload": {"stock": 15, "avg_daily_sales": 8}},
        "context": {"history_window_days": 30, "requested_by": "daily-report-workflow"},
    })
    assert res.status_code == 200
    body = res.json()
    assert body["correlation_id"] == "run_20260905_0800_001"
    assert body["action_type"] == "TICKET"
    assert body["needs_approval"] is False
    assert body["policy_matched"] == "inventory_policy.restock_auto_threshold"
