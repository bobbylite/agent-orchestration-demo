"""Agent identity: Client Credentials Grant + RFC 8693 Token Exchange.

Raw httpx calls (not a higher-level OAuth client) so the two steps stay
explicit and auditable — this is the part of the app meant to demonstrate
the protocol, not hide it.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings

TOKEN_EXCHANGE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


async def client_credentials_grant(token_endpoint: str, settings: Settings) -> dict[str, Any]:
    """Step 1: the agent authenticates as itself, producing an actor token.

    PingOne expects client_secret_basic (HTTP Basic) here, not the more
    common client_secret_post. Most PingOne worker apps also require an
    explicit `scope` — without one the grant either fails or returns a
    token with no usable access.
    """
    data: dict[str, str] = {"grant_type": "client_credentials"}
    if settings.agent_scopes:
        data["scope"] = settings.agent_scopes
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            token_endpoint,
            data=data,
            auth=(settings.agent_client_id, settings.agent_client_secret),
        )
        resp.raise_for_status()
        return resp.json()


async def token_exchange(
    token_endpoint: str,
    settings: Settings,
    *,
    subject_token: str,
    actor_token: str,
) -> dict[str, Any]:
    """Step 2: combine the user's session access token (subject) with the
    agent's own actor token into one delegated token carrying both identities.

    `scope` here requests the scope of the resulting *delegated* token —
    distinct from the actor token's own scope requested in step 1.
    """
    data = {
        "grant_type": TOKEN_EXCHANGE_GRANT_TYPE,
        "subject_token": subject_token,
        "subject_token_type": ACCESS_TOKEN_TYPE,
        "actor_token": actor_token,
        "actor_token_type": ACCESS_TOKEN_TYPE,
    }
    if settings.resolved_token_exchange_scope:
        data["scope"] = settings.resolved_token_exchange_scope
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            token_endpoint,
            data=data,
            auth=(settings.agent_client_id, settings.agent_client_secret),
        )
        resp.raise_for_status()
        return resp.json()
