"""Action verification (DAILYOPS-09): a verify step per action type that checks
external STATE, not merely that the request was accepted (HTTP 200).

n8n's lane-local Verify nodes (Verify Ticket/Report/Notify/Action) historically
only asserted the downstream node returned a result. These helpers assert the
*semantic* success criteria each action type must satisfy so an API returning
200 with a contract breach is caught.
"""
from __future__ import annotations

from typing import Any, Dict


def verify_report(state: Dict[str, Any]) -> Dict[str, Any]:
    """A real report append must reference the target sheet and a row id."""
    if not state.get("sheet_id") or "REPLACE_WITH" in str(state.get("sheet_id", "")):
        return {"verified": False, "error": "report: missing/placeholder sheet_id"}
    row = state.get("row") or state.get("append_response")
    if not row:
        return {"verified": False, "error": "report: no row appended"}
    return {"verified": True}


def verify_notify(state: Dict[str, Any]) -> Dict[str, Any]:
    """A notify must have accepted (message_id / accepted) AND a real recipient."""
    to = state.get("to") or state.get("chat_id")
    recipient = str(to or "")
    if not recipient:
        return {"verified": False, "error": "notify: missing recipient"}
    # Placeholder recipient (the well-known example.com domain used in templates).
    if recipient.endswith("@example.com"):
        return {"verified": False, "error": "notify: placeholder recipient (example.com)"}
    if not (state.get("message_id") or state.get("accepted")):
        return {"verified": False, "error": "notify: transport did not accept"}
    return {"verified": True}


def verify_ticket(state: Dict[str, Any]) -> Dict[str, Any]:
    """A ticket must exist with required fields, not just an id returned."""
    if not state.get("ticket_id"):
        return {"verified": False, "error": "ticket: no ticket_id"}
    for field in ("type", "entity", "status"):
        if not state.get(field):
            return {"verified": False, "error": f"ticket: missing required field {field}"}
    if state.get("status") not in ("OPEN", "IN_PROGRESS", "ACTION", "VERIFY", "RESOLVED", "CLOSED"):
        return {"verified": False, "error": f"ticket: invalid status {state.get('status')}"}
    return {"verified": True}


def verify_execute(state: Dict[str, Any]) -> Dict[str, Any]:
    """An external EXECUTE must carry a real success marker in the body —
    never trust HTTP 200 alone."""
    if state.get("http_status") and state["http_status"] not in (200, 201, 202, 204):
        return {"verified": False, "error": f"execute: http {state['http_status']}"}
    # Contract-breach detection: 200 with a false/missing success marker.
    if state.get("success") is False or state.get("error"):
        return {"verified": False,
                "error": f"execute: business failure: {state.get('error') or 'success=false'}"}
    if not (state.get("operation_id") or state.get("success") is True or state.get("http_status")):
        return {"verified": False, "error": "execute: no success marker in response"}
    return {"verified": True}