"""Business Analyst agent: deterministic metrics first, LLM for interpretation.

Inventory risk thresholds (days of stock coverage) are read from policies.yaml
(inventory_risk section, ARCH-003); defaults match the former hardcoded 3/7.
"""
from __future__ import annotations

import asyncio
import os
import time

import yaml

from ..tools import registry as tools
from .base import AgentResult

_DEFAULT_POLICY = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "guardrails", "policies.yaml"))
_RISK_DEFAULTS = {"low_risk_days": 3, "medium_risk_days": 7}


def _load_risk_config(path: str | None = None) -> dict:
    """Read the `inventory_risk:` section of policies.yaml; fallback to defaults."""
    cfg = dict(_RISK_DEFAULTS)
    p = path or os.environ.get("POLICY_FILE", _DEFAULT_POLICY)
    try:
        with open(p, encoding="utf-8") as f:
            section = (yaml.safe_load(f) or {}).get("inventory_risk", {}) or {}
        if isinstance(section, dict):
            for key in ("low_risk_days", "medium_risk_days"):
                val = section.get(key)
                try:
                    if val is not None:
                        cfg[key] = int(val)
                except (TypeError, ValueError):
                    pass
    except (FileNotFoundError, ValueError, TypeError):
        pass
    return cfg


async def run(event_type: str, entity: str, payload: dict, correlation_id: str = "") -> AgentResult:
    t0 = time.perf_counter()
    await asyncio.sleep(0.02)  # simulate I/O so parallelism is measurable
    stock = payload.get("stock")
    avg = payload.get("avg_daily_sales")
    if stock is None or avg is None:
        inv = tools.get_inventory(entity)
        sales = tools.get_sales_data(entity)
        stock = inv.get("stock", 15)
        avg = sales.get("avg_daily_sales", 8.0)
    try:
        avg_f = float(avg or 1) or 1.0
        days = float(stock) / avg_f
    except (TypeError, ValueError):
        return AgentResult(agent="business_analyst", status="failed",
                           summary="invalid numeric inputs", confidence=0.0,
                           duration_ms=int((time.perf_counter() - t0) * 1000))
    risk_cfg = _load_risk_config()
    low_days = risk_cfg["low_risk_days"]
    medium_days = risk_cfg["medium_risk_days"]
    low = days < low_days
    risk = "HIGH" if low else ("MEDIUM" if days < medium_days else "LOW")
    return AgentResult(
        agent="business_analyst", status="completed",
        summary=f"{entity}: stock_days ~{days:.1f} -> risk {risk}",
        findings=[f"stock={stock:g} avg_daily_sales={avg_f:g} stock_days={days:.1f}",
                  f"risk_level={risk} threshold_low={low_days}d medium={medium_days}d"],
        evidence=[{"source": "get_inventory", "content": f"stock={stock}", "score": 1.0},
                  {"source": "get_sales_data", "content": f"avg_daily_sales={avg_f}", "score": 1.0}],
        recommendations=[f"restock_{200 if low else 0}" if low else "no_restock_monitor"],
        confidence=0.91 if low else 0.75,
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )
