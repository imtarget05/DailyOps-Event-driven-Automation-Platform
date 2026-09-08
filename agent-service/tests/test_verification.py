"""DAILYOPS-09: verification per action type must check external state, not
just HTTP 200 / 'a result was returned'."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.guardrails.verifier import (  # noqa: E402
    verify_execute,
    verify_notify,
    verify_report,
    verify_ticket,
)


def test_verify_report_requires_real_sheet_and_row():
    assert verify_report({"sheet_id": "abc", "row": {"a": 1}})["verified"] is True
    assert verify_report({})["verified"] is False
    assert verify_report({"sheet_id": "REPLACE_WITH_SHEET_ID"})["verified"] is False


def test_verify_notify_requires_recipient_and_acceptance():
    assert verify_notify({"chat_id": 123, "message_id": "m1"})["verified"] is True
    assert verify_notify({"to": "ops@real-company.com", "accepted": True})["verified"] is True
    assert verify_notify({})["verified"] is False
    assert verify_notify({"to": "ops-team@example.com"})["verified"] is False


def test_verify_ticket_requires_required_fields():
    good = {"ticket_id": "tck_1", "type": "inventory_risk", "entity": "A", "status": "OPEN"}
    assert verify_ticket(good)["verified"] is True
    assert verify_ticket({"ticket_id": "tck_1", "type": "x", "entity": "A", "status": "BOGUS"})["verified"] is False
    assert verify_ticket({"ticket_id": "tck_1", "type": "x", "entity": "A"})["verified"] is False  # no status


def test_verify_execute_rejects_200_with_contract_breach():
    # R13: HTTP 200 but business operation actually failed.
    assert verify_execute({"http_status": 200, "success": True, "operation_id": "op1"})["verified"] is True
    assert verify_execute({"operation_id": "op1"})["verified"] is True
    assert verify_execute({"http_status": 200, "success": False,
                          "error": "missing required field"})["verified"] is False
    assert verify_execute({"http_status": 200, "error": "ticket created but invalid"})["verified"] is False
    assert verify_execute({"http_status": 500})["verified"] is False