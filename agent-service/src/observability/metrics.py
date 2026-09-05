"""Prometheus-style in-process metrics (counters + latency sums)."""
from __future__ import annotations

import threading
from typing import Dict

_lock = threading.Lock()
_counts: Dict[str, int] = {}
_lat_ms: Dict[str, int] = {}


def observe(agent_or_tool: str, duration_ms: int) -> None:
    with _lock:
        _counts[agent_or_tool] = _counts.get(agent_or_tool, 0) + 1
        _lat_ms[agent_or_tool] = _lat_ms.get(agent_or_tool, 0) + duration_ms


def snapshot() -> dict:
    with _lock:
        return {"counts": dict(_counts), "latency_ms_total": dict(_lat_ms)}
