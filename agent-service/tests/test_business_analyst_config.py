"""ARCH-003 (TASK-005): business analyst inventory-risk thresholds configurable.

Guarantees defaults preserve former hardcoded behavior and custom values change
risk boundaries.
"""
from __future__ import annotations

import asyncio
import pathlib

import yaml

from src.agents.business_analyst import _load_risk_config, run


def _inventory(entity: str, stock: float, avg: float):
    return asyncio.run(run("inventory_check", entity, {"stock": stock, "avg_daily_sales": avg}, "corr_ir"))


def _risk(res) -> str:
    blob = " ".join(res.findings)
    for level in ("HIGH", "MEDIUM", "LOW"):
        if f"risk_level={level}" in blob:
            return level
    return "?"


def _write_inventory(tmp_path: pathlib.Path, low: int, medium: int) -> str:
    p = tmp_path / "policies.yaml"
    p.write_text(yaml.safe_dump({"inventory_risk": {"low_risk_days": low, "medium_risk_days": medium}}),
                 encoding="utf-8")
    return str(p)


class TestConfigLoad:
    def test_defaults_when_missing_file(self):
        assert _load_risk_config(path="/nonexistent/policies.yaml") == {"low_risk_days": 3, "medium_risk_days": 7}

    def test_missing_section_falls_back_to_defaults(self, tmp_path):
        p = tmp_path / "p.yaml"
        p.write_text("policies: []\n", encoding="utf-8")
        assert _load_risk_config(path=str(p)) == {"low_risk_days": 3, "medium_risk_days": 7}

    def test_custom_values_override(self, tmp_path):
        cfg = _load_risk_config(path=_write_inventory(tmp_path, 10, 20))
        assert cfg == {"low_risk_days": 10, "medium_risk_days": 20}


class TestRiskBandsDefault:
    # stock_days = stock / avg.  Defaults: <3 HIGH, 3..7 MEDIUM, >=7 LOW.
    def test_high_below_low(self):
        assert _risk(_inventory("a", 20, 10.0)) == "HIGH"    # 2 days
        assert _risk(_inventory("b", 29, 15)) == "HIGH"      # ~1.9 days

    def test_medium_between(self):
        assert _risk(_inventory("c", 50, 10.0)) == "MEDIUM"  # 5 days

    def test_low_above_medium(self):
        assert _risk(_inventory("d", 100, 10.0)) == "LOW"    # 10 days


class TestCustomThresholds:
    def test_custom_raises_high_boundary(self, tmp_path, monkeypatch):
        # low_risk_days=50, medium=100: this entity has 40 stock-days < 50 => HIGH.
        monkeypatch.setenv("POLICY_FILE", _write_inventory(tmp_path, 50, 100))
        assert _risk(_inventory("f", 400, 10.0)) == "HIGH"   # 40 days

    def test_custom_medium_band(self, tmp_path, monkeypatch):
        # med=20: 15 days is MEDIUM (between 10 and 20), not LOW.
        monkeypatch.setenv("POLICY_FILE", _write_inventory(tmp_path, 10, 20))
        assert _risk(_inventory("g", 300, 20.0)) == "MEDIUM"  # 15 days

    def test_custom_low_still_low(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POLICY_FILE", _write_inventory(tmp_path, 10, 20))
        assert _risk(_inventory("h", 1000, 10.0)) == "LOW"   # 100 days
