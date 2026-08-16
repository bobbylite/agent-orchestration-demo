"""Client Credentials + RFC 8693 Token Exchange — same raw-httpx pattern as
backend/app/auth/agent_auth.py, deliberately duplicated rather than shared:
shared/ is for inbound-auth verification only (see CLAUDE.md), and each
service's own outbound grant calls stay separately auditable.
"""

from __future__ import annotations

from typing import Any

import httpx

TOKEN_EXCHANGE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


async def get_token_endpoint(discovery_url: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(discovery_url)
        resp.raise_for_status()
        return resp.json()["token_endpoint"]


async def client_credentials_grant(
    token_endpoint: str, *, client_id: str | None, client_secret: str | None, scope: str
) -> dict[str, Any]:
    """A client authenticates as itself, producing a token scoped to
    whatever `scope` names — which PingOne resource (and therefore which
    `aud` ends up on the token) that resolves to is determined entirely by
    PingOne's own resource configuration for that scope.
    """
    data: dict[str, str] = {"grant_type": "client_credentials", "scope": scope}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(token_endpoint, data=data, auth=(client_id or "", client_secret or ""))
        resp.raise_for_status()
        return resp.json()


async def token_exchange(
    token_endpoint: str,
    *,
    client_id: str | None,
    client_secret: str | None,
    subject_token: str,
    actor_token: str,
    scope: str,
) -> dict[str, Any]:
    """Combine a subject token (here: the already-delegated credential
    received from the Chat Agent — NOT the raw human session token, which
    this service never sees directly; RFC 8693 exchange chains preserve
    the original subject's identity through each hop, which is what keeps
    the human OBO attribution intact all the way to the MCP server) with
    an actor token (this service's own identity) into one new token scoped
    to exactly the downstream capability needed right now.
    """
    data = {
        "grant_type": TOKEN_EXCHANGE_GRANT_TYPE,
        "subject_token": subject_token,
        "subject_token_type": ACCESS_TOKEN_TYPE,
        "actor_token": actor_token,
        "actor_token_type": ACCESS_TOKEN_TYPE,
        "scope": scope,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(token_endpoint, data=data, auth=(client_id or "", client_secret or ""))
        resp.raise_for_status()
        return resp.json()
