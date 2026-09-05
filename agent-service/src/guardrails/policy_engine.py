"""Policy Engine: deterministic, rule-based. LLM never self-authorizes.

Reads policies.yaml. decide() returns (needs_approval, policy_matched).
Must live in a separate module from LLM calls — never merge into one function.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


@dataclass
class PolicyDecision:
    needs_approval: bool
    policy_matched: str
    verdict: str = "ALLOW"  # ALLOW | APPROVAL | DENY


DENY_PATTERNS = ("delete", "drop", "truncate", "shutdown", "destroy", "rm -rf")


def _load_policies(path: str | None = None) -> list[dict]:
    p = path or os.environ.get("POLICY_FILE", os.path.join(os.path.dirname(__file__), "policies.yaml"))
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("policies", [])
    except FileNotFoundError:
        return []


def check(action_type: str, recommended_action: str = "", ticket_type: str = "") -> PolicyDecision:
    """Minimal deterministic gate for the v2 action table.

    - REPORT/NOTIFY/TICKET (+ restock <= 500) -> auto
    - restock > 500, pricing changes, large refunds -> approval
    """
    ra = (recommended_action or "").lower()
    # DENY: destructive operations can never be auto-executed.
    if any(p in ra for p in DENY_PATTERNS):
        return PolicyDecision(True, "destructive_action_deny", "DENY")
    qty = None
    # parse e.g. "restock_200" -> 200
    if ra.startswith("restock"):
        try:
            qty = int("".join(c for c in ra if c.isdigit()) or 0) or None
        except ValueError:
            qty = None

    if action_type in ("REPORT", "NOTIFY", "TICKET"):
        if qty is not None and qty > 500:
            return PolicyDecision(True, "inventory_policy.restock_approval_threshold", "APPROVAL")
        matched = "ticket_auto" if action_type == "TICKET" else (
            "notify_auto" if action_type == "NOTIFY" else "report_auto")
        if ticket_type == "inventory_risk" and (qty or 0) <= 500:
            matched = "inventory_policy.restock_auto_threshold"
        return PolicyDecision(False, matched, "ALLOW")

    # EXECUTE branch — default deny to approval
    if "price" in ra or "pricing" in ra:
        return PolicyDecision(True, "pricing_change_approval", "APPROVAL")
    if "refund" in ra:
        return PolicyDecision(True, "refund_large_approval", "APPROVAL")
    if ra.startswith("restock") and (qty or 0) > 500:
        return PolicyDecision(True, "inventory_policy.restock_approval_threshold", "APPROVAL")
    return PolicyDecision(True, "default_execute_approval", "APPROVAL")
