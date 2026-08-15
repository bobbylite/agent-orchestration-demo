from __future__ import annotations

import time

import httpx
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
from app.models import AgentTokenRequest
from app.telemetry import with_span

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _pingone_error_detail(exc: httpx.HTTPStatusError) -> str:
    """PingOne's OAuth error responses are JSON `{"error": ..., "error_description": ...}`
    — surface that instead of letting the raw HTTPStatusError crash the route as an
    opaque 500. Never contains a token, only an error code/description, so it's
    safe to pass straight through (unlike the request/response bodies this
    wraps, which do carry secrets — this function only ever reads the error
    fields, nothing else)."""
    try:
        body = exc.response.json()
        return str(body.get("error_description") or body.get("error") or exc.response.text)
    except ValueError:
        return exc.response.text or str(exc)


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


_EXCHANGED_COOKIE_MAX_AGE = 8 * 60 * 60  # matches SESSION_COOKIE; individual
# tokens are re-verified against their own `exp` claim on every use anyway
# (see agentorchestration_shared.verify_bearer_token), so this is just a ceiling.


@router.post("/agent-token")
async def agent_token(
    request: Request,
    response: Response,
    body: AgentTokenRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.agent_configured:
        raise HTTPException(status_code=400, detail="Agent credentials are not configured")

    if body.scope not in settings.allowed_delegation_scopes:
        # Never request token exchange for an arbitrary client-supplied
        # scope string — only the specific, known delegation scopes this
        # deployment actually understands.
        raise HTTPException(status_code=400, detail=f"Unknown delegation scope: {body.scope!r}")

    session = read_cookie(request, SESSION_COOKIE, settings)
    if not session or not session.get("access_token"):
        raise HTTPException(status_code=401, detail="Sign in with PingOne first")

    with with_span(
        "agent.authenticate",
        {"identity.sub": session.get("sub", ""), "oauth.requested_scope": body.scope},
    ):
        metadata = await oidc.get_metadata(settings)

        with with_span("agent.client_credentials") as cc_span:
            try:
                actor_tokens = await client_credentials_grant(metadata.token_endpoint, settings)
            except httpx.HTTPStatusError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"PingOne rejected the agent's Client Credentials grant: {_pingone_error_detail(exc)}",
                ) from exc
            cc_span.set_attribute("identity.agent_client_id", settings.agent_client_id or "")
            cc_span.set_attribute("oauth.scope", settings.agent_scopes or "")

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
            try:
                exchanged = await token_exchange(
                    metadata.token_endpoint,
                    settings,
                    subject_token=session["access_token"],
                    actor_token=actor_tokens["access_token"],
                    scope=body.scope,
                )
            except httpx.HTTPStatusError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"PingOne rejected the Token Exchange request for scope {body.scope!r}: "
                    f"{_pingone_error_detail(exc)}",
                ) from exc
            te_span.set_attribute("identity.sub", session.get("sub", ""))
            te_span.set_attribute("identity.agent_client_id", settings.agent_client_id or "")
            te_span.set_attribute("oauth.scope", body.scope)

        # One cookie, keyed by scope — each entry from its own independent
        # exchange call, requested the first time an action needing it comes
        # up, not all up front. Existing scopes (from earlier approvals)
        # are preserved, not overwritten.
        exchanged_by_scope = read_cookie(request, EXCHANGED_TOKEN_COOKIE, settings) or {}
        exchanged_by_scope[body.scope] = {
            "sub": session.get("sub"),
            "client_id": settings.agent_client_id,
            "access_token": exchanged["access_token"],
            "scope": body.scope,
            "issued_at": time.time(),
        }
        set_sealed_cookie(
            response,
            EXCHANGED_TOKEN_COOKIE,
            exchanged_by_scope,
            settings,
            max_age=_EXCHANGED_COOKIE_MAX_AGE,
        )

    return {"granted_scope": body.scope, "exchanged_scopes": list(exchanged_by_scope.keys())}


@router.get("/me")
async def me(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    session = read_cookie(request, SESSION_COOKIE, settings)
    exchanged_by_scope = read_cookie(request, EXCHANGED_TOKEN_COOKIE, settings) or {}
    return {
        "oidc_enabled": settings.oidc_configured,
        "agent_enabled": settings.agent_configured,
        "signed_in": bool(session),
        "sub": (session or {}).get("sub"),
        "email": (session or {}).get("email"),
        "name": (session or {}).get("name"),
        "exchanged_scopes": list(exchanged_by_scope.keys()),
    }
