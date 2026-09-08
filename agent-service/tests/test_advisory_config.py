"""ARCH-005 (TASK-007): advisory council tunables read from policies.yaml.

Guarantees configurable grounding threshold + confidence while preserving
default behavior.
"""
from __future__ import annotations

import asyncio
import pathlib

import yaml

from src.agents.advisory_council import _ADVISORY_DEFAULTS, _load_advisory_config, run


def _write_policy(tmp_path: pathlib.Path, overrides: dict) -> str:
    p = tmp_path / "policies.yaml"
    p.write_text(yaml.safe_dump({"advisory_council": overrides}), encoding="utf-8")
    return str(p)


def test_defaults_when_missing_file():
    cfg = _load_advisory_config(path="/nonexistent/policies.yaml")
    assert cfg == _ADVISORY_DEFAULTS


def test_missing_section_falls_back_to_defaults(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("policies: []\n", encoding="utf-8")
    assert _load_advisory_config(path=str(p)) == _ADVISORY_DEFAULTS


def test_custom_values_override(tmp_path):
    cfg = _load_advisory_config(path=_write_policy(tmp_path, {"grounding_threshold": 0.5,
                                                              "grounded_confidence": 0.9}))
    assert cfg["grounding_threshold"] == 0.5
    assert cfg["grounded_confidence"] == 0.9
    assert cfg["top_k"] == _ADVISORY_DEFAULTS["top_k"] == 3  # untouched keeps default


def test_custom_grounded_confidence_applied(tmp_path, monkeypatch):
    # A grounded query under default threshold -> completed with custom confidence.
    monkeypatch.setenv("POLICY_FILE", _write_policy(tmp_path, {"grounded_confidence": 0.9}))
    res = asyncio.run(run("advisory_request", "shop",
                          {"text": "tu van chien luoc kinh doanh quy toi"}, "t_cfg_1"))
    assert res.status == "completed"
    assert res.confidence == 0.9


def test_high_grounding_threshold_forces_needs_context(tmp_path, monkeypatch):
    # grounding_threshold 0.99 makes even a grounded query fail the gate.
    monkeypatch.setenv("POLICY_FILE", _write_policy(tmp_path,
                                                    {"grounding_threshold": 0.99,
                                                     "ungrounded_confidence": 0.2}))
    res = asyncio.run(run("advisory_request", "shop",
                          {"text": "tu van chien luoc kinh doanh quy toi"}, "t_cfg_2"))
    assert res.status == "needs_context"
    assert res.confidence == 0.2
