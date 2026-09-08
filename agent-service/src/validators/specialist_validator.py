from __future__ import annotations
import re

from typing import Any, Dict, List, Optional

from ..utils.security import contains_destructive_term



def _validate_schema(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return "SCHEMA_INVALID"
    if not isinstance(result.get("agent", ""), str) or not result.get("agent", "").strip():
        return "SCHEMA_INVALID"
    if not isinstance(result.get("status", ""), str) or result.get("status", "") not in ("completed", "failed", "needs_context"):
        return "SCHEMA_INVALID"
    if not isinstance(result.get("summary", ""), str):
        return "SCHEMA_INVALID"
    if not isinstance(result.get("evidence"), list):
        return "SCHEMA_INVALID"
    if not isinstance(result.get("recommendations"), list):
        return "SCHEMA_INVALID"
    if not (0.0 <= float(result.get("confidence", 0.0)) <= 1.0):
        return "SCHEMA_INVALID"
    if not isinstance(result.get("requires_more_context", False), bool):
        return "SCHEMA_INVALID"
    if not isinstance(result.get("duration_ms", 0), int) or result.get("duration_ms", 0) < 0:
        return "SCHEMA_INVALID"
    return None


def _scan_auth_claims(text: str) -> list:
    violations = []
    patterns = [
        (re.compile(r"approved\b", re.I), "approved"),
        (re.compile(r"\bauthorized\b", re.I), "authorized"),
        (re.compile(r"\bmanager\s+approved\b", re.I), "manager_approved"),
    ]
    for pattern, claim_name in patterns:
        m = pattern.search(text or "")
        if m:
            violations.append({claim_name: m.group(0)})
    return violations


def _validate_knowledge_evidence(evidence: List[Dict[str, Any]]) -> Optional[str]:
    if not evidence:
        return "MISSING_EVIDENCE"
    for item in evidence:
        if not isinstance(item, dict):
            return "INVALID_EVIDENCE"
        source = item.get("source", "")
        content = item.get("content", "")
        score = item.get("score")
        if source not in ("knowledge_base", "policy_rag"):
            return "UNAUTHORIZED_SOURCE"
        if not isinstance(content, str) or not content.strip():
            return "INVALID_EVIDENCE"
        if score is not None and not (0.0 <= float(score) <= 1.0):
            return "INVALID_EVIDENCE"
    return None


def _validate_advisory_grounding(result: Any) -> Optional[str]:
    if not isinstance(result, dict) or result.get("status") != "completed":
        return "POLICY_INPUT_INVALID"
    evidence_scores = []
    for item in result.get("evidence", []) or []:
        if isinstance(item, dict) and "score" in item:
            try:
                evidence_scores.append(float(item["score"]))
            except (ValueError, TypeError):
                pass
    if not evidence_scores:
        return "MISSING_EVIDENCE"
    mean_score = sum(evidence_scores) / len(evidence_scores)
    if mean_score < 0.12:
        return "POLICY_INPUT_INVALID"
    return None


def _validate_action_fields(result: Any) -> Optional[str]:
    for rec in result.get("recommendations", []) or []:
        if not isinstance(rec, str):
            return "INVALID_PARAMETER"
        # ARCH-002: single authoritative destructive list (utils/security).
        if contains_destructive_term(rec):
            return "UNSUPPORTED_ACTION"
    return None


def _validate_confidence(result: Any) -> Optional[str]:
    c = result.get("confidence", 0.5)
    try:
        c = float(c)
    except (ValueError, TypeError):
        return "SUSPICIOUS_CONFIDENCE"
    if c < 0.0 or c > 1.0:
        return "SUSPICIOUS_CONFIDENCE"
    if result.get("requires_more_context", False) and c > 0.7:
        return "SUSPICIOUS_CONFIDENCE"
    return None


def validate_specialist_output(results: List[Dict[str, Any]]) -> dict:
    reject_codes: List[str] = []
    cleaned = {
        "action_type": "REPORT",
        "recommended_action": "",
        "ticket_type": "general",
        "confidence": 0.5,
        "evidence_summary": "",
        "auth_flag": "NONE",
    }
    if not results:
        return {"status": "REJECT", "reject_codes": ["SCHEMA_INVALID"], "cleaned_context": cleaned}
    for result in results:
        if not isinstance(result, dict):
            reject_codes.append("SCHEMA_INVALID")
            continue
        schema_err = _validate_schema(result)
        if schema_err:
            reject_codes.append(schema_err)
        all_text_parts = []
        if isinstance(result.get("summary"), str):
            all_text_parts.append(result.get("summary"))
        for f in result.get("findings", []) or []:
            if isinstance(f, str):
                all_text_parts.append(f)
        for rec in result.get("recommendations", []) or []:
            if isinstance(rec, str):
                all_text_parts.append(rec)
        for item in result.get("evidence", []) or []:
            if isinstance(item, dict) and isinstance(item.get("content"), str):
                all_text_parts.append(item["content"])
        combined = " ".join(all_text_parts)
        if combined:
            auth_hits = _scan_auth_claims(combined)
            if auth_hits:
                reject_codes.append("FORBIDDEN_AUTH_CLAIM")
        if result.get("agent") == "knowledge_agent":
            evid_err = _validate_knowledge_evidence(result.get("evidence", []))
            if evid_err:
                reject_codes.append(evid_err)
        if result.get("agent") == "advisory_council":
            grounds_err = _validate_advisory_grounding(result)
            if grounds_err:
                reject_codes.append(grounds_err)
        action_err = _validate_action_fields(result)
        if action_err:
            reject_codes.append(action_err)
        conf_err = _validate_confidence(result)
        if conf_err:
            reject_codes.append(conf_err)
    status = "REJECT" if any(c == "PRIVILEGE_ESCALATION" for c in reject_codes) or reject_codes else "PASS"
    if status == "REJECT":
        cleaned["auth_flag"] = "SUSPICIOUS"
    for result in results:
        result_rejects = [
            c for c in reject_codes
            if c in ("SCHEMA_INVALID", "FORBIDDEN_AUTH_CLAIM", "SEMANTIC_AUTH_CLAIM",
                     "PRIVILEGE_ESCALATION", "UNSUPPORTED_ACTION", "INVALID_PARAMETER",
                     "MISSING_EVIDENCE", "INVALID_EVIDENCE", "UNAUTHORIZED_SOURCE",
                     "SUSPICIOUS_CONFIDENCE")]
        if not result_rejects:
            cleaned["action_type"] = "REPORT"
            cleaned["recommended_action"] = result.get("summary", "")[:200] if result.get("summary") else ""
            cleaned["ticket_type"] = result.get("evidence", [{}])[0].get("source", "general") if result.get("evidence") else "general"
            cleaned["confidence"] = result.get("confidence", 0.5)
            ev_parts = []
            for item in result.get("evidence", [])[:3]:
                if isinstance(item, dict) and isinstance(item.get("content"), str):
                    ev_parts.append(item["content"][:100])
            cleaned["evidence_summary"] = " | ".join(ev_parts) if ev_parts else ""
            has_auth = any(c in ("FORBIDDEN_AUTH_CLAIM", "SEMANTIC_AUTH_CLAIM", "PRIVILEGE_ESCALATION") for c in reject_codes)
            cleaned["auth_flag"] = "SUSPICIOUS" if has_auth else "NONE"
            break
    return {"status": status, "reject_codes": reject_codes, "cleaned_context": cleaned}
