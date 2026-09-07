"""Auth hardening tests: X-API-Key required when AGENT_API_KEY is set."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.main import app  # noqa: E402

client = TestClient(app)

_DECIDE = {
    "correlation_id": "run_auth_001",
    "event": {"type": "inventory_check", "entity": "product_A",
              "payload": {"stock": 15, "avg_daily_sales": 8}},
}


def test_open_mode_when_no_key(monkeypatch):
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    res = client.post("/agent/decide", json=_DECIDE)
    assert res.status_code == 200


def test_401_without_header(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "secret-key-123")
    res = client.post("/agent/decide", json=_DECIDE)
    assert res.status_code == 401


def test_401_wrong_key(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "secret-key-123")
    res = client.post("/agent/decide", json=_DECIDE, headers={"X-API-Key": "wrong"})
    assert res.status_code == 401


def test_200_with_valid_key(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "secret-key-123")
    res = client.post("/agent/decide", json=_DECIDE, headers={"X-API-Key": "secret-key-123"})
    assert res.status_code == 200


def test_health_stays_open(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "secret-key-123")
    assert client.get("/health").status_code == 200
    # specialist endpoints protected too
    res = client.post("/agent/specialist/business_analyst",
                      json={"correlation_id": "run_auth_002",
                            "event": {"type": "inventory_check", "payload": {}}})
    assert res.status_code == 401
