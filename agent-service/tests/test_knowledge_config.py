"""ARCH-004 (TASK-006): knowledge agent tunables read from policies.yaml.

Guarantees defaults preserve the former hardcoded behavior and custom values
are respected.
"""
from __future__ import annotations

import asyncio
import pathlib

import yaml

from src.agents.knowledge_agent import _KNOWLEDGE_DEFAULTS, _load_knowledge_config, _sufficient, run


def _write_policy(tmp_path: pathlib.Path, overrides: dict) -> str:
    doc = {"knowledge_agent": overrides}
    p = tmp_path / "policies.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(p)


class TestConfigLoad:
    def test_defaults_when_missing_file(self):
        cfg = _load_knowledge_config(path="/nonexistent/policies.yaml")
        assert cfg == _KNOWLEDGE_DEFAULTS

    def test_missing_section_falls_back_to_defaults(self, tmp_path):
        p = tmp_path / "p.yaml"
        p.write_text("policies: []\n", encoding="utf-8")
        cfg = _load_knowledge_config(path=str(p))
        assert cfg == _KNOWLEDGE_DEFAULTS

    def test_custom_values_override_defaults(self, tmp_path):
        path = _write_policy(tmp_path, {"confidence_ok": 0.9, "kb_top_k": 5})
        cfg = _load_knowledge_config(path=path)
        assert cfg["confidence_ok"] == 0.9
        assert cfg["kb_top_k"] == 5
        # untouched keys keep defaults
        assert cfg["top_k"] == _KNOWLEDGE_DEFAULTS["top_k"] == 3


class TestSufficiency:
    def test_terms_parameter_controls_requirement(self):
        # "context" is not in the default term list -> generic -> sufficient
        assert _sufficient(["snippet"], "customer context please", _KNOWLEDGE_DEFAULTS["sufficient_terms"]) is True
        # with a custom term, "context" now requires evidence on "context"
        assert _sufficient(["snippet without it"], "customer context please", ["context"]) is False
        assert _sufficient(["snippet contains context"], "customer context please", ["context"]) is True

    def test_empty_snippets_never_sufficient(self):
        assert _sufficient([], "restock 200", _KNOWLEDGE_DEFAULTS["sufficient_terms"]) is False


class TestRunHonorsConfig:
    def test_default_behavior_preserved(self):
        # No env override -> config file defaults -> same as former hardcoded.
        res = asyncio.run(run("advisory_query", "all", {"text": "advise on the tiling market"}))
        assert res.confidence in (0.85, 0.4)
        scores = [e["score"] for e in res.evidence]
        assert scores  # bases 0.95/0.9 with 0.05 decay are all <= 0.95
        assert all(s <= 0.95 and s >= 0.4 for s in scores)

    def test_custom_confidence_applied(self, tmp_path, monkeypatch):
        path = _write_policy(tmp_path, {"confidence_ok": 0.99, "confidence_missing": 0.01})
        monkeypatch.setenv("POLICY_FILE", path)
        res = asyncio.run(run("advisory_query", "ref", {"text": "advise on the tiling"}))
        assert res.confidence in (0.99, 0.01)

    def test_custom_top_k_capped_and_limited(self, tmp_path, monkeypatch):
        # kb_top_k=1 -> at most 1 knowledge_base evidence item.
        path = _write_policy(tmp_path, {"kb_top_k": 1})
        monkeypatch.setenv("POLICY_FILE", path)
        res = asyncio.run(run("refund_request", "order_9",
                              {"text": "refund 1500"}, "corr_cfg"))
        kb = [e for e in res.evidence if e["source"] == "knowledge_base"]
        assert len(kb) <= 1
