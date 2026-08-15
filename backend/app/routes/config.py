from fastapi import APIRouter, Depends

from app.config import Settings, get_settings

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
async def config(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "oidc_enabled": settings.oidc_configured,
        "agent_enabled": settings.agent_configured,
        "agent_model": settings.agent_model,
    }
