"""Final PingOne Authorize decision for each MCP tool call.

The MCP server asks the same PDP as task-agent, but supplies the freshly
exchanged MCP token as ``AccessToken``. This lets the PDP evaluate the actual
agent identity, human subject, granted capability, and tool name at the
resource enforcement point.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import Settings

_worker_cache: dict[str, Any] = {}
_REFRESH_MARGIN_SECONDS = 30.0
_DECISION_MAX_ATTEMPTS = 3


async def _worker_token(settings: Settings) -> str:
    now = time.monotonic()
    cached = _worker_cache.get("access_token")
    expires_at = _worker_cache.get("expires_at", 0.0)
    if cached and now < expires_at - _REFRESH_MARGIN_SECONDS:
        return cached
    if not settings.authorize_decision_endpoint or not settings.authorize_client_id or not settings.authorize_client_secret:
        raise RuntimeError("MCP Authorize credentials are not configured")
    discovery_url = settings.oidc_discovery_url or ""
    async with httpx.AsyncClient(timeout=10) as client:
        discovery = await client.get(discovery_url)
        discovery.raise_for_status()
        token_endpoint = discovery.json()["token_endpoint"]
        data = {"grant_type": "client_credentials", "scope": settings.authorize_scope or ""}
        auth = None
        if settings.authorize_client_auth_method == "client_secret_post":
            data.update({"client_id": settings.authorize_client_id, "client_secret": settings.authorize_client_secret})
        else:
            auth = (settings.authorize_client_id, settings.authorize_client_secret)
        response = await client.post(token_endpoint, data=data, auth=auth)
        response.raise_for_status()
        token = response.json()
    _worker_cache.update(
        access_token=token["access_token"],
        expires_at=now + float(token.get("expires_in", 300)),
    )
    return token["access_token"]


async def check_tool_call(settings: Settings, *, access_token: str, tool_name: str) -> tuple[bool, str]:
    """Return PERMIT only; missing or failed PDP checks deny closed."""
    if not settings.authorize_decision_endpoint:
        return False, "authorize_not_configured"
    try:
        worker = await _worker_token(settings)
        response: httpx.Response | None = None
        for attempt in range(_DECISION_MAX_ATTEMPTS):
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    settings.authorize_decision_endpoint,
                    headers={"Authorization": f"Bearer {worker}"},
                    json={
                        "parameters": {
                            "evaluateMcpToolCall": "true",
                            "AccessToken": access_token,
                            "mcpToolName": tool_name,
                        }
                    },
                )
            if response.status_code != 429:
                response.raise_for_status()
                break
            if attempt < _DECISION_MAX_ATTEMPTS - 1:
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2**attempt
                await asyncio.sleep(delay)
        if response is None:
            return False, "authorize_request_failed:no_response"
        body = response.json()
    except (httpx.HTTPError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        detail = type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            detail = f"HTTPStatusError:{exc.response.status_code}"
            try:
                payload = exc.response.json()
                detail += f":{payload.get('error_description') or payload.get('error') or 'upstream_rejection'}"
            except ValueError:
                pass
        return False, f"authorize_request_failed:{detail}"
    if not isinstance(body, dict) or body.get("decision") != "PERMIT":
        decision = body.get("decision", "invalid") if isinstance(body, dict) else "invalid"
        return False, f"authorize_decision_{str(decision).lower()}"
    return True, "permit"
