from fastapi import APIRouter

from app.telemetry import clear_recent_spans, get_recent_spans

router = APIRouter(prefix="/api", tags=["telemetry"])


@router.get("/telemetry")
async def telemetry() -> dict:
    return {"spans": get_recent_spans()}


@router.delete("/telemetry")
async def clear_telemetry() -> dict:
    return {"cleared": clear_recent_spans()}
