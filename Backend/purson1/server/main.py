"""
CiteSafe — Citation Integrity Verification Server
===================================================
Entry point. Registers agents and mounts all routes.

Usage:
    uvicorn server.main:app --reload --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from server.api.routes import router, _handle_ws_analyze
from server.config import settings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-24s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("citesafe")


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("CiteSafe starting up...")

    _register_default_agents()

    from server.agents.base import registry
    agents = registry.list_agents()
    logger.info(f"Pipeline ready with {len(agents)} agent(s):")
    for a in agents:
        logger.info(f"  [{a['stage']}] {a['name']} — {a['description']}")

    yield

    logger.info("CiteSafe shutting down")


def _register_default_agents():
    """
    Import agent modules to trigger auto-registration.
    Add new agents here as teammates build them.
    """
    # Test agents (always available)
    from server.agents import dummy  # noqa: F401

    # Enable currently implemented agents:
    from server.agents import fetcher           # noqa: F401
    from server.agents import extractor         # noqa: F401
    from server.agents import existence         # noqa: F401
    
    # Missing agents (commented out until implemented):
    # from server.agents import embedding_gate    # noqa: F401
    # from server.agents import semantic          # noqa: F401
    # from server.agents import synthesizer       # noqa: F401


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CiteSafe",
    description="Multi-agent citation integrity verification pipeline",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes under /api prefix
app.include_router(router, prefix="/api")

# The WebSocket handler is already implemented in router at /api/analyze/ws
# but the frontend expects /ws/analyze. Let's redirect or move it.
# We'll stick to the frontend's preference for /ws/analyze at the root.

@app.websocket("/ws/analyze")
async def ws_analyze_root(websocket: WebSocket):
    from server.api.routes import _handle_ws_analyze
    await _handle_ws_analyze(websocket)

# Top-level health (outside /api prefix for k8s/monitoring)
@app.get("/health")
async def root_health():
    return {"status": "ok"}
