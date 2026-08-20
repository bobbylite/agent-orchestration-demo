"""FastAPI app hosting three things on one port (9000, unchanged):
  - the MCP endpoint itself (/mcp), for task-agent/'s agent calls
  - this service's own web UI's API (/api/auth, /api/todos, /api/audit)
  - health (/api/health)

The MCP sub-app's own lifespan (session-manager startup/shutdown) has to be
forwarded into FastAPI's lifespan or streamable-http sessions never
initialize — see fastmcp's http_app() docs. Routers are included *before*
the MCP app is mounted at "/" so they match first; the mount only catches
what nothing else claimed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.routes import router as auth_router
from app.config import get_settings
from app.mcp_server import mcp
from app.routes.audit import router as audit_router
from app.routes.health import router as health_router
from app.routes.todos import router as todos_router
from app.telemetry import clear_recent_spans, get_recent_spans, init_telemetry

mcp_app = mcp.http_app(path="/mcp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_telemetry()
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(title="Todos MCP Server", lifespan=lifespan)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(todos_router)
app.include_router(audit_router)


@app.get("/telemetry")
async def telemetry() -> dict:
    return {"spans": get_recent_spans()}


@app.delete("/telemetry")
async def clear_telemetry() -> dict:
    return {"cleared": clear_recent_spans()}


app.mount("/", mcp_app)
