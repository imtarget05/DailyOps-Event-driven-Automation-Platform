"""Read-only tools: agent-service may READ, never WRITE. n8n executes writes."""
from __future__ import annotations


def get_inventory(entity: str) -> dict:
    return {"entity": entity, "stock": 15, "note": "stub — replace with Sheets/ERP read"}


def get_sales_history(entity: str, days: int = 30) -> dict:
    return {"entity": entity, "avg_daily_sales": 8, "window_days": days, "note": "stub"}


def get_customer(entity: str) -> dict:
    return {"entity": entity, "segment": "unknown", "note": "stub"}
