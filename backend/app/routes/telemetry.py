from fastapi import APIRouter

from app.telemetry import get_recent_spans

router = APIRouter(prefix="/api", tags=["telemetry"])


@router.get("/telemetry")
async def telemetry() -> dict:
    return {"spans": get_recent_spans()}
