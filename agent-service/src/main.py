"""agent-service entrypoint: reasoning + proposal only. No writes to target systems."""
from fastapi import FastAPI

from .api.decide import router as decide_router
from .api.health import router as health_router
from .api.specialists import router as specialists_router

app = FastAPI(title="DailyOps agent-service", version="0.3.0")
app.include_router(decide_router)
app.include_router(specialists_router)
app.include_router(health_router)
