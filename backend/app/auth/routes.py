from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.auth import oidc
from app.auth.agent_auth import client_credentials_grant, token_exchange
from app.auth.pkce import (
    OIDC_STATE_COOKIE,
    generate_pkce_pair,
    generate_token,
    seal_state,
    unseal_state,
)
from app.auth.session import (
    AGENT_TOKEN_COOKIE,
    EXCHANGED_TOKEN_COOKIE,
    SESSION_COOKIE,
    clear_cookie,
    read_cookie,
    set_sealed_cookie,
)
from app.config import Settings, get_settings
from app.telemetry import with_span

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _redirect_uri(settings: Settings) -> str:
    return f"{settings.app_base_url}/api/auth/callback"


@router.get("/login")
async def login(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    if not settings.oidc_configured:
        raise HTTPException(status_code=400, detail="OIDC is not configured")

    with with_span("oidc.login.redirect") as span:
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
        span.set_attribute("oidc.issuer", metadata.issuer)
        return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    with with_span("oidc.login.callback") as span:
        span.set_attribute("oidc.has_error", bool(error))
        if error or not code or not state:
            return RedirectResponse(f"{settings.frontend_origin}/?auth_error=1")

        raw_state_cookie = request.cookies.get(OIDC_STATE_COOKIE)
        saved = unseal_state(raw_state_cookie, settings) if raw_state_cookie else None
        if not saved or saved.get("state") != state:
            span.set_attribute("oidc.state_valid", False)
            return RedirectResponse(f"{settings.frontend_origin}/?auth_error=state_mismatch")
        span.set_attribute("oidc.state_valid", True)

        metadata = await oidc.get_metadata(settings)
        tokens = await oidc.exchange_code_for_tokens(
            metadata,
            settings,
            code=code,
            redirect_uri=_redirect_uri(settings),
            code_verifier=saved["verifier"],
        )
        claims = await oidc.verify_id_token(metadata, settings, tokens["id_token"], nonce=saved["nonce"])
        span.set_attribute("identity.sub", claims.get("sub", ""))

        response = RedirectResponse(settings.frontend_origin)
        clear_cookie(response, OIDC_STATE_COOKIE, settings)
        set_sealed_cookie(
            response,
            SESSION_COOKIE,
            {
                "sub": claims.get("sub"),
                "email": claims.get("email"),
                "name": claims.get("name"),
                "access_token": tokens["access_token"],
                "id_token": tokens["id_token"],
                "issued_at": time.time(),
            },
            settings,
        )
        return response


@router.get("/logout")
async def logout(request: Request, settings: Settings = Depends(get_settings)) -> RedirectResponse:
    with with_span("oidc.logout") as span:
        session = read_cookie(request, SESSION_COOKIE, settings)
        span.set_attribute("identity.sub", (session or {}).get("sub", ""))

        end_session_endpoint = None
        if settings.oidc_configured:
            try:
                metadata = await oidc.get_metadata(settings)
                end_session_endpoint = metadata.end_session_endpoint
            except Exception:
                end_session_endpoint = None

        target = settings.frontend_origin
        rp_initiated = bool(end_session_endpoint and settings.oidc_post_logout_redirect_uri)
        if rp_initiated:
            target = (
                f"{end_session_endpoint}?client_id={settings.oidc_client_id}"
                f"&post_logout_redirect_uri={settings.oidc_post_logout_redirect_uri}"
            )
        span.set_attribute("oidc.rp_initiated", rp_initiated)

        response = RedirectResponse(target)
        clear_cookie(response, SESSION_COOKIE, settings)
        clear_cookie(response, AGENT_TOKEN_COOKIE, settings)
        clear_cookie(response, EXCHANGED_TOKEN_COOKIE, settings)
        return response


@router.post("/agent-token")
async def agent_token(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.agent_configured:
        raise HTTPException(status_code=400, detail="Agent credentials are not configured")

    session = read_cookie(request, SESSION_COOKIE, settings)
    if not session or not session.get("access_token"):
        raise HTTPException(status_code=401, detail="Sign in with PingOne first")

    with with_span(
        "agent.authenticate",
        {"identity.sub": session.get("sub", "")},
    ):
        metadata = await oidc.get_metadata(settings)

        with with_span("agent.client_credentials") as cc_span:
            actor_tokens = await client_credentials_grant(metadata.token_endpoint, settings)
            cc_span.set_attribute("identity.agent_client_id", settings.agent_client_id or "")

        set_sealed_cookie(
            response,
            AGENT_TOKEN_COOKIE,
            {
                "client_id": settings.agent_client_id,
                "access_token": actor_tokens["access_token"],
                "issued_at": time.time(),
            },
            settings,
            max_age=actor_tokens.get("expires_in", 3600),
        )

        with with_span("agent.token_exchange") as te_span:
            exchanged = await token_exchange(
                metadata.token_endpoint,
                settings,
                subject_token=session["access_token"],
                actor_token=actor_tokens["access_token"],
            )
            te_span.set_attribute("identity.sub", session.get("sub", ""))
            te_span.set_attribute("identity.agent_client_id", settings.agent_client_id or "")

        set_sealed_cookie(
            response,
            EXCHANGED_TOKEN_COOKIE,
            {
                "sub": session.get("sub"),
                "client_id": settings.agent_client_id,
                "access_token": exchanged["access_token"],
                "issued_at": time.time(),
            },
            settings,
            max_age=exchanged.get("expires_in", 3600),
        )

    return {"agent_authenticated": True, "exchanged": True}


@router.get("/me")
async def me(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    session = read_cookie(request, SESSION_COOKIE, settings)
    agent = read_cookie(request, AGENT_TOKEN_COOKIE, settings)
    exchanged = read_cookie(request, EXCHANGED_TOKEN_COOKIE, settings)
    return {
        "oidc_enabled": settings.oidc_configured,
        "agent_enabled": settings.agent_configured,
        "signed_in": bool(session),
        "sub": (session or {}).get("sub"),
        "email": (session or {}).get("email"),
        "name": (session or {}).get("name"),
        "agent_authenticated": bool(agent),
        "exchanged": bool(exchanged),
    }
