from __future__ import annotations

import os

# Must run before any `a2a.*` import — a2a-sdk auto-instruments its own
# internal classes (EventQueue, request handlers, JSON-RPC transports —
# anything decorated with @trace_class/@trace_function in
# a2a/utils/telemetry.py) onto whatever TracerProvider is registered,
# reading this env var exactly once, at that module's import time. Once
# init_telemetry() below registers a real provider, every EventQueue
# enqueue/dequeue and RPC-transport call would otherwise show up as noise
# in the Telemetry panel alongside our own inbound_auth.verify /
# a2a.task_execute / judge.evaluate spans. See CLAUDE.md.
os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")

from contextlib import asynccontextmanager

import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI

from app.agent_executor import TaskAgentExecutor
from app.card import build_agent_card
from app.config import get_settings
from app.telemetry import get_recent_spans, init_telemetry


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_telemetry()
    settings = get_settings()
    agent_card = build_agent_card(settings)
    executor = TaskAgentExecutor(settings)
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler, "/"),
    )
    yield


app = FastAPI(title="Task Agent (A2A)", lifespan=lifespan)


@app.get("/telemetry")
async def telemetry() -> dict:
    """Same shape as backend/app/routes/telemetry.py's `/api/telemetry` —
    the frontend fetches this via the vite dev proxy (see
    frontend/vite.config.ts), same-origin, so no CORS middleware is needed
    here."""
    return {"spans": get_recent_spans()}


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)
