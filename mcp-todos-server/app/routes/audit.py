from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app import audit
from app.auth.session import SESSION_COOKIE, read_cookie
from app.config import Settings, get_settings

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit")
async def get_audit(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    if not read_cookie(request, SESSION_COOKIE, settings):
        raise HTTPException(status_code=401, detail="Sign in with PingOne first")
    return {"entries": audit.get_recent()}


@router.delete("/audit")
async def clear_audit(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    if not read_cookie(request, SESSION_COOKIE, settings):
        raise HTTPException(status_code=401, detail="Sign in with PingOne first")
    return {"cleared": audit.clear()}
