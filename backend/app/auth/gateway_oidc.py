"""OAuth helpers for the AI Gateway's user Authorization Code flow."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings


def authorization_url(
    settings: Settings,
    *,
    state: str,
    code_challenge: str,
    redirect_uri: str,
    client_id: str | None = None,
) -> str:
    # Only the endpoints are required here — the client_id comes from the
    # process's startup DCR registration (app/auth/gateway_dcr.py), not the
    # (possibly empty) GATEWAY_CLIENT_ID seed.
    if not settings.gateway_authorization_endpoint:
        raise RuntimeError("AI Gateway OAuth is not configured")
    if not client_id and not settings.gateway_client_id:
        raise RuntimeError("AI Gateway OAuth has no client_id (DCR registration did not run)")
    params = {
        "response_type": "code",
        # Explicit client_id wins over the configured value — routes pass the
        # process-registered DCR client (app/auth/gateway_dcr.py) so login
        # always uses THIS process's own client, not a stale configured one.
        "client_id": client_id or settings.gateway_client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.gateway_scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{settings.gateway_authorization_endpoint}?{urlencode(params)}"


async def exchange_code(
    settings: Settings,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str | None = None,
) -> dict[str, Any]:
    if not settings.gateway_token_endpoint:
        raise RuntimeError("AI Gateway OAuth is not configured")
    if not client_id and not settings.gateway_client_id:
        raise RuntimeError("AI Gateway OAuth has no client_id (DCR registration did not run)")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "client_id": client_id or settings.gateway_client_id,
    }
    auth = None
    if settings.gateway_client_auth_method == "client_secret_post":
        data["client_secret"] = settings.gateway_client_secret or ""
    elif settings.gateway_client_auth_method != "none":
        raise RuntimeError(f"Unsupported gateway client auth method: {settings.gateway_client_auth_method}")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(settings.gateway_token_endpoint, data=data, auth=auth)
        response.raise_for_status()
        return response.json()


async def refresh_token(
    settings: Settings,
    refresh_token_value: str,
    *,
    client_id: str | None = None,
) -> dict[str, Any]:
    if not settings.gateway_token_endpoint:
        raise RuntimeError("AI Gateway OAuth is not configured")
    if not client_id and not settings.gateway_client_id:
        raise RuntimeError("AI Gateway OAuth has no client_id (DCR registration did not run)")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_value,
        "client_id": client_id or settings.gateway_client_id,
    }
    if settings.gateway_client_auth_method == "client_secret_post":
        data["client_secret"] = settings.gateway_client_secret or ""
    elif settings.gateway_client_auth_method != "none":
        raise RuntimeError(f"Unsupported gateway client auth method: {settings.gateway_client_auth_method}")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(settings.gateway_token_endpoint, data=data)
        response.raise_for_status()
        return response.json()
