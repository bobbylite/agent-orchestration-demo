"""PingOne CIBA backchannel authentication.

The browser never receives the CIBA request id or access token. The routes
keep both values server-side and expose only an opaque approval id/status.
"""

from __future__ import annotations

import secrets
import string
from typing import Any

import httpx

CIBA_GRANT_TYPE = "urn:openid:params:grant-type:ciba"


def generate_binding_message(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(1, min(length, 8))))


class CibaError(Exception):
    """A safe, user-facing CIBA failure without token material."""


def error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        return str(body.get("error_description") or body.get("error") or "PingOne rejected the CIBA request")
    except ValueError:
        return "PingOne rejected the CIBA request"


def _auth_kwargs(settings, method: str) -> tuple[dict[str, str], dict[str, Any]]:
    if method == "client_secret_basic":
        return {}, {"auth": (settings.ciba_client_id or "", settings.ciba_client_secret or "")}
    if method == "client_secret_post":
        return {"client_id": settings.ciba_client_id or "", "client_secret": settings.ciba_client_secret or ""}, {}
    if method == "none":
        return {}, {}
    raise CibaError("Unsupported CIBA client authentication method")


async def start(settings, *, login_hint: str, binding_message: str | None = None) -> dict[str, Any]:
    required = (
        settings.ciba_authorization_endpoint,
        settings.ciba_token_endpoint,
        settings.ciba_client_id,
        settings.ciba_client_secret,
    )
    if not all(required):
        raise CibaError("CIBA is not configured")
    if not login_hint.strip():
        raise CibaError("The signed-in user has no CIBA login hint")

    auth_data, auth_kwargs = _auth_kwargs(settings, settings.ciba_authorization_auth_method)
    data = {
        **auth_data,
        "login_hint": login_hint,
        "binding_message": binding_message or generate_binding_message(settings.ciba_binding_message_length),
        "requested_expiry": str(settings.ciba_requested_expiry),
        "scope": settings.ciba_scope,
    }
    async with httpx.AsyncClient(timeout=settings.ciba_http_timeout) as client:
        response = await client.post(settings.ciba_authorization_endpoint, data=data, **auth_kwargs)
    if response.is_error:
        raise CibaError(error_detail(response))
    try:
        body = response.json()
    except ValueError as exc:
        raise CibaError("PingOne returned an invalid CIBA authorization response") from exc
    auth_req_id = body.get("auth_req_id")
    if not isinstance(auth_req_id, str) or not auth_req_id:
        raise CibaError("PingOne did not return a CIBA authorization request id")
    try:
        expires_in = max(1, min(int(body.get("expires_in", settings.ciba_requested_expiry)), settings.ciba_requested_expiry))
        interval = max(settings.ciba_min_poll_interval, float(body.get("interval", 2)))
    except (TypeError, ValueError) as exc:
        raise CibaError("PingOne returned invalid CIBA polling metadata") from exc
    return {"auth_req_id": auth_req_id, "expires_in": expires_in, "interval": min(interval, settings.ciba_max_poll_interval)}


async def poll(settings, auth_req_id: str) -> tuple[str, dict[str, Any] | None, float | None]:
    """Poll once. Returns ``pending``, ``approved``, or ``terminal``."""
    if not auth_req_id:
        raise CibaError("Missing CIBA authorization request")
    auth_data, auth_kwargs = _auth_kwargs(settings, settings.ciba_token_auth_method)
    data = {**auth_data, "grant_type": CIBA_GRANT_TYPE, "auth_req_id": auth_req_id}
    async with httpx.AsyncClient(timeout=settings.ciba_http_timeout) as client:
        response = await client.post(settings.ciba_token_endpoint, data=data, **auth_kwargs)
    try:
        body = response.json()
    except ValueError as exc:
        raise CibaError("PingOne returned an invalid CIBA token response") from exc

    if response.is_success and isinstance(body.get("access_token"), str) and body["access_token"]:
        return "approved", body, None
    error = body.get("error")
    if error == "authorization_pending":
        return "pending", None, None
    if error == "slow_down":
        return "pending", None, 2.0
    if error in {"access_denied", "expired_token", "invalid_grant"}:
        return "terminal", None, None
    raise CibaError(error_detail(response))
