from __future__ import annotations

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


app = FastAPI(title="AgentCore Console (LangGraph)", lifespan=lifespan)

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
