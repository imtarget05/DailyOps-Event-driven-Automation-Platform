"""Deterministic approval/webhook gate logic (Python mirror of n8n guard nodes).

Kept in agent-service so the intended semantics are testable in CI even though
the live enforcement runs in n8n Code/Postgres nodes:

- classify_approval_decision: DAILYOPS-03 — only exact tokens 'approve'/'reject'
  map to APPROVED/REJECTED. Everything else ('approved', 'TRUE', 'APPROVED BUT...',
  '', None) is inert (0 rows updated -> stays PENDING -> no execute).
- approval_expired: DAILYOPS-04 — PENDING past expires_at must escalate, never
  sit PENDING forever.
- classify_outcome: DAILYOPS-08 — UNKNOWN_OUTCOME (timeout; side-effect may have
  happened) vs FAILED (definitive). UNKNOWN_OUTCOME must reconcile before retry;
  blind retry of side-effects is never allowed.
- verify_webhook_token: DAILYOPS-06 — HMAC-SHA256(correlation_id, secret) check
  for human approval links.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

_DECISION_MAP = {"approve": "APPROVED", "reject": "REJECTED"}


def classify_approval_decision(raw: object) -> str | None:
    """Exact-token whitelist. Returns 'APPROVED'/'REJECTED' or None (inert)."""
    if not isinstance(raw, str):
        return None
    return _DECISION_MAP.get(raw.strip().lower())


def approval_expired(status: str, expires_at: datetime | str | None,
                     now: datetime | None = None) -> bool:
    """True only for PENDING rows whose TTL has passed."""
    if status != "PENDING" or expires_at is None:
        return False
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now >= expires_at


_TIMEOUT_PATTERNS = (
    "timeout", "timed out", "timed_out", "econnaborted", "etimedout",
    "econnreset", "socket hang up", "aborted",
)


def classify_outcome(error_text: object) -> str:
    """UNKNOWN_OUTCOME if the failure signature looks like a timeout/abort
    (side-effect may already have happened), else FAILED."""
    err = str(error_text or "").lower()
    if any(p in err for p in _TIMEOUT_PATTERNS):
        return "UNKNOWN_OUTCOME"
    return "FAILED"


def sign_approval_token(correlation_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), str(correlation_id).encode(), hashlib.sha256).hexdigest()


def verify_webhook_token(correlation_id: str, token: object, secret: str) -> bool:
    """Constant-time HMAC check for the human approval link."""
    if not token or not isinstance(token, str):
        return False
    expected = sign_approval_token(correlation_id, secret)
    return hmac.compare_digest(token.strip(), expected)
