import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def _decide(text: str) -> dict:
    res = client.post("/agent/decide", json={
        "correlation_id": "run_tg_test",
        "event": {"type": "telegram_chat", "entity": "98765",
                  "payload": {"text": text, "chat_id": 98765, "source": "telegram"}},
    })
    assert res.status_code == 200
    return res.json()


def test_telegram_smalltalk_replies_notify():
    body = _decide("chào shop ơi")
    assert body["action_type"] == "NOTIFY"
    assert body["needs_approval"] is False


def test_telegram_stock_creates_ticket_auto():
    body = _decide("kho còn bao nhiêu, hết hàng chưa?")
    assert body["action_type"] == "TICKET"
    assert body["needs_approval"] is False
    assert body["policy_matched"] == "inventory_policy.restock_auto_threshold"


def test_telegram_refund_requires_approval():
    body = _decide("cho mình hoàn tiền đơn hàng nhé")
    assert body["action_type"] == "EXECUTE"
    assert body["needs_approval"] is True
