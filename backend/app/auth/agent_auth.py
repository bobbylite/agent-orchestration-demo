"""Agent identity: Client Credentials Grant + RFC 8693 Token Exchange.

Raw httpx calls (not a higher-level OAuth client) so the two steps stay
explicit and auditable — this is the part of the app meant to demonstrate
the protocol, not hide it.
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
    PingOne's own resource configuration for that scope, not by anything
    passed here; same pattern as every other scope this app requests.

    Takes `client_id`/`client_secret` explicitly rather than reading a
    single fixed pair off Settings — the Chat Agent uses a different
    PingOne worker app to prove its own identity (step 1) than the one
    authorized to perform Token Exchange (step 2); see
    app/auth/routes.py's /agent-token for which pair goes where.

    PingOne expects client_secret_basic (HTTP Basic) here, not the more
    common client_secret_post. Most PingOne worker apps also require an
    explicit `scope` — without one the grant either fails or returns a
    token with no usable access.
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
    """Combine the user's session access token (subject) with an actor
    token into one delegated token carrying both identities. `scope` here
    is always the generic delegation scope (`Settings.agent_delegation_scope`)
    — this service doesn't request an action-specific scope like
    "todos:read"; whichever agent receives this token performs its own
    further exchange for that.

    `client_id`/`client_secret` authenticate the request itself — the
    PingOne worker app authorized to *perform* Token Exchange, which is a
    distinct app from the one that produced `actor_token` in step 1; see
    app/auth/routes.py's /agent-token.
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
