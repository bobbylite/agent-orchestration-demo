"""Agent identity: Client Credentials Grant + RFC 8693 Token Exchange.

Duplicated from backend/app/auth/agent_auth.py — deliberately, matching
this repo's "each service owns its outbound grant calls independently"
convention (see also task-agent/app/token_grants.py). Raw httpx calls, not
a higher-level OAuth client, so the two steps stay explicit and auditable.
"""

from __future__ import annotations

from typing import Any

import httpx

TOKEN_EXCHANGE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


async def client_credentials_grant(
    token_endpoint: str, *, client_id: str | None, client_secret: str | None, scope: str
) -> dict[str, Any]:
    """A client authenticates as itself, producing a token scoped to
    whatever `scope` names — which PingOne resource (and therefore which
    `aud` ends up on the token) that resolves to is determined entirely by
    PingOne's own resource configuration for that scope.

    PingOne expects client_secret_basic (HTTP Basic) here, not the more
    common client_secret_post.
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
    """Combine the user's own access token (subject) with an actor token
    into one delegated token carrying both identities. `scope` is always
    the generic delegation scope (`Settings.agent_delegation_scope`) — this
    bridge doesn't request an action-specific scope like "todos:read"; the
    Task Agent performs its own further exchange for that once it knows
    what's actually being asked.
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
