from __future__ import annotations

import os

# Must run before any `a2a.*` import — a2a-sdk auto-instruments its own
# internal classes (JSON-RPC client transport, etc. — anything decorated
# with @trace_class/@trace_function in a2a/utils/telemetry.py) onto
# whatever TracerProvider is registered, reading this env var exactly
# once, at that module's import time. `app/agent/tools.py` (imported
# transitively below, via app.agent.graph) uses the A2A client to call the
# Task Agent — once init_telemetry() registers a real provider, those
# client-transport calls would otherwise show up as noise in the
# Telemetry panel alongside our own agent.invoke / inbound_auth.verify /
# agent.a2a_delegate spans. See CLAUDE.md.
os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.graph import build_graph
from app.auth.routes import router as auth_router
from app.config import get_settings
from app.routes.config import router as config_router
from app.routes.health import router as health_router
from app.routes.invoke import router as invoke_router
from app.routes.telemetry import router as telemetry_router
from app.telemetry import init_telemetry


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_telemetry()
    settings = get_settings()
    app.state.graph = build_graph(settings)
    yield


app = FastAPI(title="Agent Orchestration Console (LangGraph)", lifespan=lifespan)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(config_router)
app.include_router(auth_router)
app.include_router(invoke_router)
app.include_router(telemetry_router)
