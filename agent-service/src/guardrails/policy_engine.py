"""Policy Engine: deterministic, rule-based. LLM never self-authorizes.

Reads policies.yaml. decide() returns (needs_approval, policy_matched).
Must live in a separate module from LLM calls — never merge into one function.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

import yaml


@dataclass
class PolicyDecision:
    needs_approval: bool
    policy_matched: str
    verdict: str = "ALLOW"  # ALLOW | APPROVAL | DENY


@dataclass
class PolicyRequest:
    """Typed gate input (DAILYOPS-02). The ONLY fields the policy engine reads.
    LLM proposals must be funneled through here — never pass raw LLM dicts."""
    action_type: str
    recommended_action: str = ""
    ticket_type: str = ""


# DAILYOPS-02: keys that would constitute an LLM-injected self-authorization
# claim. These are stripped from LLM output before any policy input is derived;
# the engine never reads them and must never start reading them.
AUTHZ_CLAIM_KEYS = frozenset({
    "authorized", "approved", "needs_approval", "policy_verdict",
    "can_execute", "permission_granted", "is_allowed", "approval_status",
})

# DAILYOPS-11: semantic authorization claim classification.
# Beyond stripping exact key names (AUTHZ_CLAIM_KEYS), we also check for
# authorization language embedded in free-text fields such as `reason`,
# `summary`, `reply_text`, or `recommended_action`.  An LLM can attempt
# privilege escalation without using the exact keywords, e.g.
# "reason": "The manager already approved this operation."  This function
# classifies the severity of any detected claims.
#
# Classification values:
#   NONE           — no authorization-related language detected
#   SUSPICIOUS     — language resembles an claim but is ambiguous/ context-dependent
#   PRIVILEGE_ESC — language explicitly claims privileged authorization
#
# The classification guides the policy gate: NONE → proceed normally,
# SUSPICIOUS → fail closed (require trusted server-side authorization),
# PRIVILEGE_ESC → hard block (reject action outright).
AUTH_CLAIM_CLASSIFICATION = Literal["NONE", "SUSPICIOUS", "PRIVILEGE_ESC"]


# Regex patterns ordered from most specific to most general.
# Each tuple: (compiled_pattern, classification)
_AUTH_PATTERNS: list[tuple[re.Pattern[str], AUTH_CLAIM_CLASSIFICATION]] = [
    # PRIVILEGE_ESC: explicit privileged authorization claims
    (re.compile(r"\bmanager\s+already\s+approved\b", re.I), "PRIVILEGE_ESC"),
    (re.compile(r"\badministrator\s+has\s+implicitly\s+authorized\b", re.I), "PRIVILEGE_ESC"),
    (re.compile(r"\bthe\s+user\s+has\s+existing\s+permissions\b", re.I), "PRIVILEGE_ESC"),
    (re.compile(r"\bcover.*by.*user.*existing.*permissions\b", re.I), "PRIVILEGE_ESC"),
    # SUSPICIOUS: ambiguous authorization language
    (re.compile(r"\bapproved\b", re.I), "SUSPICIOUS"),
    (re.compile(r"\bauthorized\b", re.I), "SUSPICIOUS"),
    (re.compile(r"\bapproved\s+by\b", re.I), "SUSPICIOUS"),
    (re.compile(r"\bauthorized\s+by\b", re.I), "SUSPICIOUS"),
    (re.compile(r"\bmanager\s+approved\b", re.I), "SUSPICIOUS"),
    (re.compile(r"\bauthorized\s+operator\b", re.I), "SUSPICIOUS"),
    # NONE: no authorization language (these patterns explicitly match
    # non-claim words so they can be excluded from the claim-hunting logic)
    (re.compile(r"\bapprove\b", re.I), "NONE"),    # "approve" verb, not "approved" claim
    (re.compile(r"\bapproval\b", re.I), "NONE"),   # "approval" noun, not a claim
]


def _classify_auth_claims(text: str) -> dict[AUTH_CLAIM_CLASSIFICATION, list[str]]:
    """Classify any authorization-related language in free text.

    Returns a dict mapping each classification to the list of matched
    strings.  Always includes at least {"NONE": [...]} even when claims
    are found, because the regex set contains NONE-patterns that match
    legitimate words like "approve" and "approval".
    """
    result: dict[AUTH_CLAIM_CLASSIFICATION, list[str]] = {"NONE": []}
    for pattern, classification in _AUTH_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            result.setdefault(classification, []).extend(matches)
    # Deduplicate NONE entries
    result["NONE"] = list(dict.fromkeys(result.get("NONE", [])))
    return result


def _detect_semantic_auth_claims(text: str) -> dict[AUTH_CLAIM_CLASSIFICATION, list[str]]:
    """Return the full classification of authorization-related language.

    The caller should use the returned classification to decide whether
    to proceed, fail closed, or hard-block the action.
    """
    return _classify_auth_claims(text)


def assert_no_authorization_claims(proposal) -> dict:
    """DAILYOPS-02: strip every LLM-injected authorization claim key.

    Returns {} for any non-dict input (poisoned LLM output) so the caller
    never sees a fabricated claim and can fail closed. Proposal content
    other than the claim keys is preserved.
    """
    if not isinstance(proposal, dict):
        return {}
    return {k: v for k, v in proposal.items() if k not in AUTHZ_CLAIM_KEYS}


def _enforce_auth_claims(proposal: dict) -> tuple[dict, AUTH_CLAIM_CLASSIFICATION]:
    """Classify auth claims in a proposal and return the cleaned dict + classification.

    Returns (cleaned_proposal, classification).  The classification is
    one of:
      NONE         → no claims detected; proceed with normal policy gate
      SUSPICIOUS   → claim language was found but is ambiguous; fail closed
      PRIVILEGE_ESC→ explicit privileged claim detected; hard block
    """
    # Scan all string values of known claim-prone fields.
    combined_text_parts: list[str] = []
    for field in ("reason", "summary", "reply_text", "recommended_action"):
        val = proposal.get(field)
        if isinstance(val, str):
            combined_text_parts.append(val)
    combined = " ".join(combined_text_parts)

    classification = "NONE"
    if combined:
        classification_result = _detect_semantic_auth_claims(combined)
        # Prioritize: PRIVILEGE_ESC > SUSPICIOUS > NONE
        if "PRIVILEGE_ESC" in classification_result and classification_result["PRIVILEGE_ESC"]:
            classification = "PRIVILEGE_ESC"
        elif "SUSPICIOUS" in classification_result and classification_result["SUSPICIOUS"]:
            classification = "SUSPICIOUS"
        # If NONE only, keep as NONE

    # Strip exact authorization claim keys (AUTHZ_CLAIM_KEYS) regardless.
    cleaned = assert_no_authorization_claims(proposal)

    return cleaned, classification


def check_request(req: PolicyRequest, path: str | None = None) -> PolicyDecision:
    """Typed entrypoint — same deterministic rules as check()."""
    return check(req.action_type, req.recommended_action, req.ticket_type, path)


# DAILYOPS-01: every action resolves to exactly ONE of 4 risk groups.
RISK_LEVELS = ("READ", "LOW-RISK_WRITE", "HIGH-RISK_WRITE", "EXTERNAL_SIDE_EFFECT")

# action_type -> risk group (deterministic mapping; no action unclassified).
_ACTION_RISK = {
    "REPORT": "LOW-RISK_WRITE",      # append row to internal sheet
    "NOTIFY": "LOW-RISK_WRITE",      # telegram/email message
    "TICKET": "LOW-RISK_WRITE",      # internal ops ticket
    "EXECUTE": "EXTERNAL_SIDE_EFFECT",  # target API / ERP / CRM mutation
}


def classify_risk(action_type: str) -> str:
    """Map an action to one of the 4 risk groups. Unknown -> highest risk."""
    return _ACTION_RISK.get((action_type or "").upper(), "EXTERNAL_SIDE_EFFECT")


# Destructive verbs matched as whole tokens (split on non-alphanumeric) so
# "delete_system_data" is denied but "deadline"/"readme" are not false-positives.
DENY_TOKENS = {"delete", "drop", "truncate", "shutdown", "destroy", "purge", "wipe", "rm", "rf"}

_DEFAULT_RESTOCK_AUTO_MAX = 500


def _load_policies(path: str | None = None) -> list[dict]:
    p = path or os.environ.get("POLICY_FILE", os.path.join(os.path.dirname(__file__), "policies.yaml"))
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("policies", [])
    except FileNotFoundError:
        return []


def _load_thresholds(path: str | None = None) -> dict:
    p = path or os.environ.get("POLICY_FILE", os.path.join(os.path.dirname(__file__), "policies.yaml"))
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        t = data.get("thresholds", {}) or {}
        return {"restock_auto_max": int(t.get("restock_auto_max", _DEFAULT_RESTOCK_AUTO_MAX)),
                "finance_refund_auto_max": int(t.get("finance_refund_auto_max", 200))}
    except (FileNotFoundError, ValueError, TypeError):
        return {"restock_auto_max": _DEFAULT_RESTOCK_AUTO_MAX,
                "finance_refund_auto_max": 200}


def is_destructive(recommended_action: str) -> bool:
    """Token-boundary destructive check (no substring false-positives).
    Coerces non-string input (poisoned LLM output) instead of raising."""
    tokens = {t for t in re.split(r"[^a-z0-9]+", str(recommended_action or "").lower()) if t}
    return bool(tokens & DENY_TOKENS)


def _parse_restock_qty(recommended_action: str) -> int | None:
    """Parse e.g. 'restock_200' / 'restock qty 200' -> 200 (None if absent)."""
    return _parse_leading_amount(recommended_action, "restock")


def _parse_leading_amount(recommended_action: str, prefix: str) -> int | None:
    # DAILYOPS-OPS-4: a poisoned LLM may return a non-string (dict/list) here —
    # coerce so it degrades to "no amount parsed" instead of raising AttributeError.
    ra = str(recommended_action or "").lower()
    if not ra.startswith(prefix):
        return None
    digits = "".join(c for c in ra if c.isdigit())
    try:
        return int(digits) if digits else None
    except ValueError:
        return None


def check(action_type: str, recommended_action: str = "", ticket_type: str = "",
          path: str | None = None, **_ignored_authorization_claims) -> PolicyDecision:
    """Deterministic gate for the v2 action table.

    - REPORT/NOTIFY/TICKET (+ restock <= restock_auto_max) -> auto
    - restock > threshold, pricing changes, large refunds -> approval
    Thresholds come from policies.yaml `thresholds:` — never hard-coded here.
    DAILYOPS-02: any extra kwargs (e.g. `authorized=True`, `approved='APPROVED'`
    injected from an LLM output) are explicitly SWALLOWED here — the engine
    reads only the canonical args above and must never grow claim-reading logic.
    """
    # DAILYOPS-OPS-4: a non-string recommended_action (dict/list from a poisoned
    # LLM) must never silently pass a numeric threshold. Before the str() coercion
    # it crashed (500); after the coercion it parsed as "no amount" (= 0, under
    # every threshold) and auto-allowed. Fail closed instead: force approval.
    if not isinstance(recommended_action, str):
        return PolicyDecision(True, "malformed_recommended_action", "APPROVAL")
    ra = (recommended_action or "").lower()
    qty = _parse_restock_qty(ra)
    refund_amt = _parse_leading_amount(ra, "refund")
    thresholds = _load_thresholds(path)
    restock_max = thresholds["restock_auto_max"]
    refund_max = thresholds["finance_refund_auto_max"]

    # DENY: destructive operations can never be auto-executed.
    if is_destructive(ra):
        return PolicyDecision(True, "destructive_action_deny", "DENY")

    # ADVISORY: council only advises (REPORT/NOTIFY). Never TICKET/EXECUTE,
    # even if LLM proposes it — prevents hallucinated side effects.
    if ticket_type == "advisory":
        if action_type in ("REPORT", "NOTIFY"):
            return PolicyDecision(False, "advisory_report_only", "ALLOW")
        return PolicyDecision(True, "advisory_report_only", "DENY")

    if action_type in ("REPORT", "NOTIFY", "TICKET"):
        if qty is not None and qty > restock_max:
            return PolicyDecision(True, "inventory_policy.restock_approval_threshold", "APPROVAL")
        if ticket_type == "finance_refund" and refund_amt is not None and refund_amt > refund_max:
            return PolicyDecision(True, "finance_refund_approval", "APPROVAL")
        matched = "ticket_auto" if action_type == "TICKET" else (
            "notify_auto" if action_type == "NOTIFY" else "report_auto")
        if ticket_type == "inventory_risk" and (qty or 0) <= restock_max:
            matched = "inventory_policy.restock_auto_threshold"
        if ticket_type == "finance_refund" and (refund_amt or 0) <= refund_max:
            matched = "finance_refund_auto_note"
        return PolicyDecision(False, matched, "ALLOW")

    # EXECUTE branch — default deny to approval
    if "price" in ra or "pricing" in ra:
        return PolicyDecision(True, "pricing_change_approval", "APPROVAL")
    if "refund" in ra:
        if refund_amt is not None and refund_amt > refund_max:
            return PolicyDecision(True, "finance_refund_approval", "APPROVAL")
        return PolicyDecision(True, "refund_large_approval", "APPROVAL")
    if "crm_bulk" in ra or ticket_type == "crm_bulk_update":
        return PolicyDecision(True, "crm_bulk_update_approval", "APPROVAL")
    if "export" in ra and "crm" in ra:
        return PolicyDecision(True, "crm_data_export_approval", "APPROVAL")
    if ticket_type == "marketing_spend":
        return PolicyDecision(True, "marketing_budget_spend_approval", "APPROVAL")
    if ticket_type == "hr_offboarding":
        return PolicyDecision(True, "hr_offboarding_approval", "APPROVAL")
    if qty is not None and qty > restock_max:
        return PolicyDecision(True, "inventory_policy.restock_approval_threshold", "APPROVAL")
    return PolicyDecision(True, "default_execute_approval", "APPROVAL")

