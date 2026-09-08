"""agent-service entrypoint: reasoning + proposal only. No writes to target systems."""
from __future__ import annotations

import os

from fastapi import Depends, FastAPI

from .api.auth import require_api_key
from .api.decide import router as decide_router
from .api.health import router as health_router
from .api.specialists import router as specialists_router


def _enforce_production_secrets() -> None:
    """DAILYOPS-OPS-2: refuse to boot in production with empty secrets.

    Mirrors the credential-preflight pattern in infra/n8n/init.sh. Running with
    AGENT_SERVICE_ENV=production and no AGENT_API_KEY would silently open the
    /agent/* endpoints to the network — fail fast instead.
    """
    if os.environ.get("AGENT_SERVICE_ENV", "dev").strip().lower() != "production":
        return
    missing = [k for k in ("AGENT_API_KEY",) if not os.environ.get(k, "").strip()]
    if missing:
        raise SystemExit(
            f"DAILYOPS-OPS-2 FATAL: production mode requires non-empty secret(s): "
            f"{', '.join(missing)}. Set them before deploying."
        )


_enforce_production_secrets()

app = FastAPI(title="DailyOps agent-service", version="0.4.0")
# /agent/* requires X-API-Key when AGENT_API_KEY is set; /health stays open.
app.include_router(decide_router, dependencies=[Depends(require_api_key)])
app.include_router(specialists_router, dependencies=[Depends(require_api_key)])
app.include_router(health_router)

