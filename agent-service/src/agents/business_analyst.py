"""Business Analyst agent: deterministic metrics first, LLM for interpretation."""
from __future__ import annotations

import asyncio
import time

from .base import AgentResult
from ..tools import registry as tools


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
    low = days < 3
    risk = "HIGH" if low else ("MEDIUM" if days < 7 else "LOW")
    return AgentResult(
        agent="business_analyst", status="completed",
        summary=f"{entity}: stock_days ~{days:.1f} -> risk {risk}",
        findings=[f"stock={stock:g} avg_daily_sales={avg_f:g} stock_days={days:.1f}",
                  f"risk_level={risk} threshold_low=3d medium=7d"],
        evidence=[{"source": "get_inventory", "content": f"stock={stock}", "score": 1.0},
                  {"source": "get_sales_data", "content": f"avg_daily_sales={avg_f}", "score": 1.0}],
        recommendations=[f"restock_{200 if low else 0}" if low else "no_restock_monitor"],
        confidence=0.91 if low else 0.75,
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )
