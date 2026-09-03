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
import logging

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

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_telemetry()
    settings = get_settings()
    app.state.graph = build_graph(settings)
    if settings.gateway_configured:
        # Register the gateway OAuth client (RFC 7591) NOW, once, and keep
        # it for the process's entire lifetime — not per-login. The gateway
        # forgets its DCR registrations on its own internal schedule, so a
        # fresh process always mints its own client rather than trusting
        # GATEWAY_CLIENT_ID (which is a seed/last-known value, not source
        # of truth). Registered here so the very first browser login already
        # hits a client the gateway recognizes. Failure is logged and
        # non-fatal: the same registration re-runs lazily from the first
        # gateway login (ensure_client_registered is called there too).
        from app.auth import gateway_dcr
        from app.telemetry import with_span

        try:
            with with_span("gateway.dcr.startup") as span:
                client_id = await gateway_dcr.ensure_client_registered(settings)
                # client_id is a public identifier — safe on a span.
                span.set_attribute("gateway.client_id", client_id)
        except gateway_dcr.GatewayRegistrationError as exc:
            logger.warning(
                "Gateway client registration failed at startup (will retry on "
                "first gateway login): %s",
                exc,
            )
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
