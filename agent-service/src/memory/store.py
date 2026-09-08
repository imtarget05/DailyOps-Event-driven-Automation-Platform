"""Bounded in-memory context: recent decisions per correlation/chat. No full-history dumps."""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Deque, Dict, List

_MAX_ENTRIES = 50
_store: Dict[str, Deque[Dict[str, Any]]] = {}


def remember(key: str, entry: Dict[str, Any]) -> None:
    dq = _store.setdefault(key, deque(maxlen=_MAX_ENTRIES))
    dq.append({"ts": time.time(), **entry})


def recall(key: str, limit: int = 5) -> List[Dict[str, Any]]:
    dq = _store.get(key, deque())
    return list(dq)[-limit:]


def memory_key(event_type: str, entity: str, payload: dict) -> str:
    chat = payload.get("chat_id") or payload.get("from") or ""
    if chat:
        return f"chat:{chat}"
    return f"entity:{entity or event_type}"
