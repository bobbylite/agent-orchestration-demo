"""Sign-in for a human opening this service's own UI — mirrors
backend/app/auth/routes.py's login/callback/logout/me shape, trimmed to
just that (no client-credentials/token-exchange here: this service is the
resource being called, not something that delegates further).

The one addition over backend's version: a successful /callback also
records the human's sub -> {email, name} into app/identity.py's cache —
the "session cache fallback" half of OBO identity resolution.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import identity
from app.auth import oidc
from app.auth.pkce import (
    OIDC_STATE_COOKIE,
    generate_pkce_pair,
    generate_token,
    seal_state,
    unseal_state,
)
from app.auth.session import SESSION_COOKIE, clear_cookie, read_cookie, set_sealed_cookie
from app.config import Settings, get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _redirect_uri(settings: Settings) -> str:
    return f"{settings.app_base_url}/api/auth/callback"


@router.get("/login")
async def login(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    if not settings.oidc_configured:
        raise HTTPException(status_code=400, detail="OIDC is not configured")

    metadata = await oidc.get_metadata(settings)
    verifier, challenge = generate_pkce_pair()
    state = generate_token()
    nonce = generate_token()

    response = RedirectResponse(
        oidc.build_authorization_url(
            metadata,
            settings,
            redirect_uri=_redirect_uri(settings),
            state=state,
            nonce=nonce,
            code_challenge=challenge,
        )
    )
    response.set_cookie(
        key=OIDC_STATE_COOKIE,
        value=seal_state({"state": state, "nonce": nonce, "verifier": verifier}, settings),
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.app_base_url.startswith("https"),
        path="/",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if error or not code or not state:
        return RedirectResponse(f"{settings.frontend_origin}/?auth_error=1")

    raw_state_cookie = request.cookies.get(OIDC_STATE_COOKIE)
    saved = unseal_state(raw_state_cookie, settings) if raw_state_cookie else None
    if not saved or saved.get("state") != state:
        return RedirectResponse(f"{settings.frontend_origin}/?auth_error=state_mismatch")

    metadata = await oidc.get_metadata(settings)
    tokens = await oidc.exchange_code_for_tokens(
        metadata,
        settings,
        code=code,
        redirect_uri=_redirect_uri(settings),
        code_verifier=saved["verifier"],
    )
    claims = await oidc.verify_id_token(metadata, settings, tokens["id_token"], nonce=saved["nonce"])

    identity.remember_login(claims.get("sub"), email=claims.get("email"), name=claims.get("name"))

    response = RedirectResponse(settings.frontend_origin)
    clear_cookie(response, OIDC_STATE_COOKIE, settings)
    set_sealed_cookie(
        response,
        SESSION_COOKIE,
        {
            "sub": claims.get("sub"),
            "email": claims.get("email"),
            "name": claims.get("name"),
            "issued_at": time.time(),
        },
        settings,
    )
    return response


@router.get("/logout")
async def logout(request: Request, settings: Settings = Depends(get_settings)) -> RedirectResponse:
    end_session_endpoint = None
    if settings.oidc_configured:
        try:
            metadata = await oidc.get_metadata(settings)
            end_session_endpoint = metadata.end_session_endpoint
        except Exception:
            end_session_endpoint = None

    target = settings.frontend_origin
    if end_session_endpoint and settings.oidc_post_logout_redirect_uri:
        target = (
            f"{end_session_endpoint}?client_id={settings.oidc_client_id}"
            f"&post_logout_redirect_uri={settings.oidc_post_logout_redirect_uri}"
        )

    response = RedirectResponse(target)
    clear_cookie(response, SESSION_COOKIE, settings)
    return response


@router.get("/me")
async def me(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    session = read_cookie(request, SESSION_COOKIE, settings)
    return {
        "oidc_enabled": settings.oidc_configured,
        "signed_in": bool(session),
        "sub": (session or {}).get("sub"),
        "email": (session or {}).get("email"),
        "name": (session or {}).get("name"),
    }
