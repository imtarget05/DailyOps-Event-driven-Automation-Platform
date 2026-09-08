"""API-key auth for agent-service (n8n <-> agent-service contract).

- If AGENT_API_KEY is set, every /agent/* request must carry header
  `X-API-Key: <value>` (constant-time comparison).
- If AGENT_API_KEY is empty/unset the service runs in open (dev) mode so
  local smoke tests keep working; production deployments MUST set it.
- /health stays unauthenticated (used by docker healthcheck + LB probes).
"""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def auth_is_enforced() -> bool:
    return bool(os.environ.get("AGENT_API_KEY", "").strip())


async def require_api_key(key: str | None = Security(_api_key_header)) -> None:
    expected = os.environ.get("AGENT_API_KEY", "").strip()
    if not expected:
        return  # open mode (dev only)
    if not key or not hmac.compare_digest(key.strip(), expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
