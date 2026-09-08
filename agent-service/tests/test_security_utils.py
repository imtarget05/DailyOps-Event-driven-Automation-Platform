"""Tests for the centralized destructive-content utility (ARCH-002, TASK-001/002).

Guarantees the shared utility produces the same verdicts the three former
local implementations produced, plus edge cases (empty, non-string, case).
"""
from __future__ import annotations

import asyncio

import pytest

from src.guardrails import policy_engine as pe
from src.utils import security as sec


class TestAuthoritativeList:
    def test_union_of_former_local_lists(self):
        # Every term formerly detected by any call site is still covered.
        former = {"delete", "drop", "truncate", "shutdown", "destroy",
                  "purge", "wipe", "rm", "rf", "rm -rf"}
        covered = set(sec.DESTRUCTIVE_TOKENS) | set(sec.DESTRUCTIVE_PHRASES)
        assert former <= covered

    def test_token_and_phrase_modes_agree_on_shared_verbs(self):
        for verb in sec.DESTRUCTIVE_VERBS:
            assert sec.is_destructive(verb) is True
            assert sec.contains_destructive_term(verb) is True


class TestTokenMode:
    @pytest.mark.parametrize("action", [
        "delete_system_data", "drop_table", "shutdown_service",
        "purge_logs", "wipe_disk", "truncate_users", "rm_rf",
    ])
    def test_detects_destructive_action_ids(self, action):
        assert sec.is_destructive(action) is True

    @pytest.mark.parametrize("action", [
        "append_deadline_row", "send_email_flyer", "restock_200",
        "advisory_brief", "refund_150", "",
    ])
    def test_benign_action_ids_pass(self, action):
        assert sec.is_destructive(action) is False


class TestPhraseMode:
    def test_rm_rf_phrase_detected(self):
        assert sec.contains_destructive_term("run rm -rf /") is True

    def test_single_letter_tokens_do_not_substring_match_prose(self):
        # rm/rf inside ordinary words must NOT flag free text
        # (behavior parity with the word-boundary validator matcher).
        assert sec.contains_destructive_term("please confirm the information") is False

    def test_word_boundary_matches(self):
        assert sec.contains_destructive_term("please DELETE the staging table") is True
        assert sec.contains_destructive_term("do not truncate anything") is True
        assert sec.contains_destructive_term("wipe the cache") is True

    def test_benign_prose_passes(self):
        assert sec.contains_destructive_term("append the daily report row") is False
        assert sec.contains_destructive_term("create a restock ticket") is False


class TestEdgeCases:
    @pytest.mark.parametrize("bad", [None, 123, {"a": 1}, ["delete"], True, object()])
    def test_non_string_coerced_not_raised(self, bad):
        # Poisoned LLM output must never crash a detector; both modes
        # coerce to str and return a plain bool.
        assert isinstance(sec.is_destructive(bad), bool)
        assert isinstance(sec.contains_destructive_term(bad), bool)

    def test_empty_string(self):
        assert sec.is_destructive("") is False
        assert sec.contains_destructive_term("") is False

    def test_mixed_case(self):
        assert sec.is_destructive("Delete_System_Data") is True
        assert sec.is_destructive("SHUTDOWN_service") is True
        assert sec.contains_destructive_term("Truncate Everything") is True

    def test_punctuation_and_whitespace_boundaries(self):
        assert sec.is_destructive("delete!") is True
        assert sec.is_destructive("  drop  ") is True
        assert sec.contains_destructive_term("drop.") is True


class TestCallSiteParity:
    def test_policy_engine_reuses_utility(self):
        # The policy engine must expose the SHARED implementation (TASK-001).
        assert pe.is_destructive is sec.is_destructive

    def test_policy_verdict_unchanged(self):
        d = pe.check("EXECUTE", "delete_system_data")
        assert (d.verdict, d.policy_matched) == ("DENY", "destructive_action_deny")

    def test_operations_agent_flags_destructive(self):
        from src.agents import operations_agent as ops
        res = asyncio.run(ops.run("system_maintenance", "db", {"text": "please drop the staging tables"}))
        assert res.recommendations == ["deny"]

    def test_operations_agent_passes_benign(self):
        from src.agents import operations_agent as ops
        res = asyncio.run(ops.run("inventory_check", "store_1", {"text": "restock sku 42"}))
        assert res.recommendations == ["create_restock_request(entity=store_1, qty=200)"]

    def test_validator_rejects_destructive_recommendation(self):
        from src.validators.specialist_validator import validate_specialist_output
        out = validate_specialist_output([{
            "agent": "operations_agent", "status": "completed", "summary": "ok",
            "evidence": [], "recommendations": ["truncate_logs_now"], "confidence": 0.9,
        }])
        assert "UNSUPPORTED_ACTION" in out["reject_codes"]

    def test_validator_allows_benign_recommendation(self):
        from src.validators.specialist_validator import validate_specialist_output
        out = validate_specialist_output([{
            "agent": "report_agent", "status": "completed", "summary": "ok",
            "evidence": [], "recommendations": ["append_daily_row"], "confidence": 0.9,
        }])
        assert "UNSUPPORTED_ACTION" not in out["reject_codes"]
