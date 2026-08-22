from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.auth import oidc
from app.auth.agent_auth import client_credentials_grant, token_exchange
from app.auth import ciba, ciba_store
from app.auth.ciba import CibaError
from app.auth.pkce import (
    OIDC_STATE_COOKIE,
    generate_pkce_pair,
    generate_token,
    seal_state,
    unseal_state,
)
from app.auth.session import (
    AGENT_TOKEN_COOKIE,
    CIBA_TOKEN_COOKIE,
    EXCHANGED_TOKEN_COOKIE,
    SESSION_COOKIE,
    clear_cookie,
    read_cookie,
    set_sealed_cookie,
)
from app.auth import token_ledger
from app.auth.token_decode import decode_token_claims
from app.config import Settings, get_settings
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
        clear_cookie(response, CIBA_TOKEN_COOKIE, settings)
        ciba_store.clear_for_session((session or {}).get("sub", ""))
        return response


_CIBA_COOKIE_MAX_AGE = 60 * 60
_EXCHANGED_COOKIE_MAX_AGE = 8 * 60 * 60  # matches SESSION_COOKIE; individual
# tokens are re-verified against their own `exp` claim on every use anyway
# (see agentorchestration_shared.verify_bearer_token), so this is just a ceiling.


async def _mint_agent_token(request: Request, response: Response | None, settings: Settings, session: dict) -> str:
    if not settings.agent_configured:
        raise HTTPException(status_code=400, detail="Agent credentials are not configured")
    with with_span("agent.authenticate", {"identity.sub": session.get("sub", "")}):
        metadata = await oidc.get_metadata(settings)
        with with_span("agent.client_credentials") as cc_span:
            try:
                actor_token = await client_credentials_grant(
                    metadata.token_endpoint,
                    client_id=settings.agent_client_id,
                    client_secret=settings.agent_client_secret,
                    scope=settings.agent_own_scope,
                )
            except httpx.HTTPStatusError as exc:
                raise HTTPException(status_code=502, detail=f"PingOne rejected the agent's Client Credentials grant: {_pingone_error_detail(exc)}") from exc
            cc_span.set_attribute("identity.agent_client_id", settings.agent_client_id or "")
            cc_span.set_attribute("oauth.scope", settings.agent_own_scope)
            token_ledger.record("agent_own", actor_token["access_token"])
        with with_span("agent.token_exchange") as te_span:
            try:
                delegated = await token_exchange(
                    metadata.token_endpoint,
                    client_id=settings.agent_delegation_client_id,
                    client_secret=settings.agent_delegation_client_secret,
                    subject_token=session["access_token"],
                    actor_token=actor_token["access_token"],
                    scope=settings.agent_delegation_scope,
                )
            except httpx.HTTPStatusError as exc:
                raise HTTPException(status_code=502, detail=f"PingOne rejected the Token Exchange request: {_pingone_error_detail(exc)}") from exc
            te_span.set_attribute("identity.sub", session.get("sub", ""))
            te_span.set_attribute("identity.agent_client_id", settings.agent_delegation_client_id or "")
            te_span.set_attribute("oauth.scope", settings.agent_delegation_scope)
            token_ledger.record("delegation", delegated["access_token"])
        if response is not None:
            set_sealed_cookie(response, EXCHANGED_TOKEN_COOKIE, {
            "sub": session.get("sub"), "client_id": settings.agent_delegation_client_id,
            "access_token": delegated["access_token"], "scope": settings.agent_delegation_scope,
            "issued_at": time.time(),
        }, settings, max_age=_EXCHANGED_COOKIE_MAX_AGE)
    return delegated["access_token"]


@router.post("/agent-token")
async def agent_token(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Two-step RFC 8693 delegation, both steps scoped generically — this
    service doesn't know or care which specific downstream action
    (todos:read, todos:write, or anything else) is intended; that's the
    Task Agent's job once it holds this credential and performs its own
    exchange. This endpoint's only job: prove the Chat Agent's own
    identity (Client Credentials, `agent_own_scope`, audience = this
    service's own URL), then combine that with the user's session into one
    delegation credential (Token Exchange, `agent_delegation_scope`,
    audience = the Task Agent's URL).
    """
    session = read_cookie(request, SESSION_COOKIE, settings)
    if not session or not session.get("access_token"):
        raise HTTPException(status_code=401, detail="Sign in with PingOne first")
    await _mint_agent_token(request, response, settings, session)
    return {"delegated": True}


@router.post("/ciba/start")
async def ciba_start(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    session = read_cookie(request, SESSION_COOKIE, settings)
    if not session or not session.get("access_token") or not session.get("sub"):
        raise HTTPException(status_code=401, detail="Sign in with PingOne first")
    try:
        started = await ciba.start(settings, login_hint=session.get("email") or session["sub"])
    except CibaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    item = ciba_store.add(
        session_sub=session["sub"], access_token=session["access_token"],
        auth_req_id=started["auth_req_id"], expires_in=started["expires_in"], interval=started["interval"],
    )
    return {"approval_id": item.approval_id, "interval": item.interval, "expires_in": started["expires_in"]}


@router.post("/ciba/poll")
async def ciba_poll(request: Request, response: Response, approval_id: str, settings: Settings = Depends(get_settings)) -> dict:
    session = read_cookie(request, SESSION_COOKIE, settings)
    if not session or not session.get("access_token") or not session.get("sub"):
        raise HTTPException(status_code=401, detail="Sign in with PingOne first")
    item = ciba_store.get(approval_id)
    if not item or not ciba_store.owns(item, session_sub=session["sub"], access_token=session["access_token"]):
        raise HTTPException(status_code=404, detail="CIBA approval not found")
    async with item.lock:
        if item.status == "expired":
            ciba_store.remove(approval_id)
            return {"status": "expired"}
        if item.status == "approved":
            return {"status": "approved"}
        if time.monotonic() < item.next_poll_at:
            return {"status": "pending", "retry_after": max(0.1, item.next_poll_at - time.monotonic())}
        try:
            status, token_body, slow_down = await ciba.poll(settings, item.auth_req_id)
        except CibaError as exc:
            item.status = "denied"
            ciba_store.remove(approval_id)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if status == "pending":
            if slow_down:
                item.interval = min(item.interval + slow_down, settings.ciba_max_poll_interval)
            item.next_poll_at = time.monotonic() + item.interval
            return {"status": "pending", "retry_after": item.interval}
        if status == "terminal" or not token_body:
            item.status = "denied"
            ciba_store.remove(approval_id)
            return {"status": "denied"}
        item.status = "approved"
        item.token = token_body["access_token"]
        set_sealed_cookie(response, CIBA_TOKEN_COOKIE, {"access_token": item.token, "sub": session["sub"], "issued_at": time.time()}, settings, max_age=min(_CIBA_COOKIE_MAX_AGE, int(token_body.get("expires_in", _CIBA_COOKIE_MAX_AGE))))
        await _mint_agent_token(request, response, settings, session)
        ciba_store.remove(approval_id)
        return {"status": "approved"}


@router.get("/me")
async def me(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    session = read_cookie(request, SESSION_COOKIE, settings)
    delegated = read_cookie(request, EXCHANGED_TOKEN_COOKIE, settings)
    return {
        "oidc_enabled": settings.oidc_configured,
        "agent_enabled": settings.agent_configured,
        "signed_in": bool(session),
        "sub": (session or {}).get("sub"),
        "email": (session or {}).get("email"),
        "name": (session or {}).get("name"),
        "agent_delegated": bool(delegated),
    }


@router.get("/token-chain")
async def token_chain(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    """Backs the frontend's Token Chain inspector — decoded claims (+ raw
    compact JWT, for the panel's "reveal" toggle) for every token THIS
    service has actually seen. `user` is decoded live from the session
    cookie on every call (cheap, no network call, nothing to cache); `agent_own`/
    `delegation` come from token_ledger's last-real-/agent-token-call
    snapshot — null if the user has never clicked "Approve Agent Action" this
    process's lifetime, deliberately NOT triggered by opening this panel
    (see token_ledger.py's docstring — this endpoint only ever reports what
    already happened, it never mints anything new)."""
    session = read_cookie(request, SESSION_COOKIE, settings)
    user_entry = None
    if session and session.get("access_token"):
        user_entry = {"raw": session["access_token"], "claims": decode_token_claims(session["access_token"])}
    ledger = token_ledger.snapshot()
    return {"user": user_entry, "agent_own": ledger["agent_own"], "delegation": ledger["delegation"]}
