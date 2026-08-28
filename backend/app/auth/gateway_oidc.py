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
) -> str:
    if not settings.gateway_authorization_endpoint or not settings.gateway_client_id:
        raise RuntimeError("AI Gateway OAuth is not configured")
    params = {
        "response_type": "code",
        "client_id": settings.gateway_client_id,
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
) -> dict[str, Any]:
    if not settings.gateway_token_endpoint or not settings.gateway_client_id:
        raise RuntimeError("AI Gateway OAuth is not configured")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "client_id": settings.gateway_client_id,
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


async def refresh_token(settings: Settings, refresh_token_value: str) -> dict[str, Any]:
    if not settings.gateway_token_endpoint or not settings.gateway_client_id:
        raise RuntimeError("AI Gateway OAuth is not configured")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_value,
        "client_id": settings.gateway_client_id,
    }
    if settings.gateway_client_auth_method == "client_secret_post":
        data["client_secret"] = settings.gateway_client_secret or ""
    elif settings.gateway_client_auth_method != "none":
        raise RuntimeError(f"Unsupported gateway client auth method: {settings.gateway_client_auth_method}")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(settings.gateway_token_endpoint, data=data)
        response.raise_for_status()
        return response.json()
