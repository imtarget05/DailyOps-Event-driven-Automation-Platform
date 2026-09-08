"""Reliability/chaos tests: approval fuzz (DAILYOPS-03), approval TTL (04),
idempotent webhook replays (05), webhook auth (06), retry/unknown-outcome (08).

These mirror the deterministic policies enforced by the n8n guard nodes; the
Python implementations live in src/guardrails/approval_gate.py.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from src.guardrails.approval_gate import (  # noqa: E402
    approval_expired,
    classify_approval_decision,
    classify_outcome,
    sign_approval_token,
    verify_webhook_token,
)
from src.main import app  # noqa: E402

client = TestClient(app)


# ------------------------------------------- DAILYOPS-03: approval string fuzz

def test_approval_exact_tokens_only():
    assert classify_approval_decision("approve") == "APPROVED"
    assert classify_approval_decision("APPROVE") == "APPROVED"  # canonical token, case-insensitive input OK
    assert classify_approval_decision("reject") == "REJECTED"


def test_approval_fuzz_variants_are_inert():
    # EVERY variant below must NOT execute: mapped to None -> UPDATE matches 0 rows.
    for bad in ("approved", "APPROVED", "ApPrOvEd", "approve but", "APPROVED BUT...",
                "true", "TRUE", True, 1, "1", "yes", "ok", "accept", "",
                "   ", None, "approve;", "approve -- drop table", "appove"):
        assert classify_approval_decision(bad) is None, f"{bad!r} must be rejected"


def test_approval_reject_variants_are_inert():
    for bad in ("rejected", "REJECTED", "reject it", "deny", "false", None, ""):
        assert classify_approval_decision(bad) is None, f"{bad!r} must not map to REJECTED"


# ------------------------------------------- DAILYOPS-04: approval TTL

_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def test_pending_past_ttl_is_expired():
    assert approval_expired("PENDING", _NOW - timedelta(hours=25), now=_NOW) is True
    assert approval_expired("PENDING", _NOW - timedelta(minutes=1), now=_NOW) is True


def test_pending_within_ttl_is_not_expired():
    assert approval_expired("PENDING", _NOW + timedelta(hours=24), now=_NOW) is False


def test_decided_rows_never_expire():
    past = _NOW - timedelta(days=7)
    assert approval_expired("APPROVED", past, now=_NOW) is False
    assert approval_expired("REJECTED", past, now=_NOW) is False
    assert approval_expired("ESCALATED", past, now=_NOW) is False


def test_missing_expires_at_is_not_expired():
    assert approval_expired("PENDING", None, now=_NOW) is False


def test_expired_parsing_from_iso_string():
    iso = (_NOW - timedelta(hours=2)).isoformat()
    assert approval_expired("PENDING", iso, now=_NOW) is True


# ------------------------------------------- DAILYOPS-05: idempotent replays

_DECIDE = {
    "correlation_id": "run_reliability_idem",
    "event": {"type": "inventory_check", "entity": "product_A",
              "payload": {"stock": 15, "avg_daily_sales": 8}},
}


def test_webhook_replay_5x_yields_same_idempotency_key():
    keys = [client.post("/agent/decide", json=_DECIDE).json()["idempotency_key"]
            for _ in range(5)]
    assert len(set(keys)) == 1 and all(keys), "5 replays must collapse onto 1 key"


def test_different_correlation_ids_yield_different_keys():
    a = client.post("/agent/decide", json=_DECIDE).json()["idempotency_key"]
    b = client.post("/agent/decide", json={**_DECIDE,
                    "correlation_id": "run_reliability_idem_other"}).json()["idempotency_key"]
    assert a != b


# ------------------------------------------- DAILYOPS-06: webhook token auth

def test_webhook_token_valid():
    secret = "s3cret"
    token = sign_approval_token("corr_1", secret)
    assert verify_webhook_token("corr_1", token, secret) is True


def test_webhook_token_forged_or_missing_is_rejected():
    secret = "s3cret"
    assert verify_webhook_token("corr_1", "deadbeef", secret) is False          # forged
    assert verify_webhook_token("corr_1", "", secret) is False                  # missing
    assert verify_webhook_token("corr_1", None, secret) is False                # missing
    assert verify_webhook_token("corr_2",
                                sign_approval_token("corr_1", secret), secret) is False  # wrong corr
    assert verify_webhook_token("corr_1",
                                sign_approval_token("corr_1", "other"), secret) is False  # wrong secret


# ------------------------------------------- DAILYOPS-08: unknown outcome vs failed

def test_timeout_is_unknown_outcome_not_failed():
    for err in ("Request failed: timeout after 20000ms", "socket hang up",
                "ETIMEDOUT", "ECONNABORTED", "request aborted"):
        assert classify_outcome(err) == "UNKNOWN_OUTCOME", err


def test_definitive_errors_are_failed():
    for err in ("HTTP 400 Bad Request", "HTTP 500 Internal Server Error",
                "connection refused", ""):
        assert classify_outcome(err) == "FAILED", err


def test_unknown_outcome_policy_forbids_blind_retry():
    # The retry policy contract: unknown -> reconcile first; failed -> no auto retry.
    assert classify_outcome("timeout") == "UNKNOWN_OUTCOME"
    assert classify_outcome("HTTP 500") == "FAILED"
