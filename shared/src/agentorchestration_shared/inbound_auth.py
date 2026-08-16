"""Inbound auth, shared by every agent service in this repo.

Verifies a bearer token the same way AWS Bedrock AgentCore's inbound auth
(a JWT authorizer) would: signature against the issuer's JWKS, issuer,
expiry, and audience, checked fresh on every call. There is no notion of a
"session" here — every service that calls this independently re-verifies
the token itself; none of them extend implicit trust to another service's
prior verification.

This module is intentionally self-contained (its own OIDC discovery/JWKS
fetch+cache) rather than depending on any one service's Settings class, so
it can be shared unmodified across services that otherwise have nothing in
common.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

_discovery_cache: dict[str, dict[str, str]] = {}
_jwks_cache: dict[str, tuple[float, KeySet]] = {}
_JWKS_TTL_SECONDS = 3600


class InboundAuthError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class VerifiedIdentity:
    sub: str | None
    client_id: str | None  # whichever client authenticated THIS request (e.g. performed a Token Exchange) — a mechanical detail, not necessarily "the agent"
    actor_sub: str | None  # RFC 8693 `act.sub`, if the issuer populates it
    scope: str | None  # raw `scope` claim, space-delimited per RFC 6749 §3.3
    email: str | None = None  # `email` or `preferred_username`, if the resource maps one onto the token
    aud: str | None = None  # the specific audience value this token was verified against
    # PingOne-populated custom claim, propagated from the *actor* token used
    # in whatever Token Exchange produced this token — i.e. "which agent is
    # actually delegating," as opposed to `client_id` above (which app
    # happened to perform the exchange call). This is what per-agent policy
    # ACLs (task-agent/app/policy.py, mcp-todos-server/app/policy.py) should
    # check identity against. Confirmed present on a real exchanged token
    # 2026-08-16 — not present on every token shape (e.g. a plain Client
    # Credentials actor token has no reason to carry it), so treat as optional.
    agent_client_id: str | None = None

    def has_scope(self, required: str) -> bool:
        return required in (self.scope or "").split()


async def _get_issuer_and_jwks_uri(discovery_url: str) -> tuple[str, str]:
    if discovery_url not in _discovery_cache:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            doc = resp.json()
        _discovery_cache[discovery_url] = {"issuer": doc["issuer"], "jwks_uri": doc["jwks_uri"]}
    doc = _discovery_cache[discovery_url]
    return doc["issuer"], doc["jwks_uri"]


async def _get_jwks(jwks_uri: str) -> KeySet:
    cached = _jwks_cache.get(jwks_uri)
    now = time.time()
    if cached and now - cached[0] < _JWKS_TTL_SECONDS:
        return cached[1]
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        key_set = KeySet.import_key_set(resp.json())
    _jwks_cache[jwks_uri] = (now, key_set)
    return key_set


async def verify_bearer_token(token: str, *, discovery_url: str, expected_audience: str) -> VerifiedIdentity:
    if not expected_audience:
        raise InboundAuthError("no_expected_audience_configured")

    issuer, jwks_uri = await _get_issuer_and_jwks_uri(discovery_url)

    try:
        key_set = await _get_jwks(jwks_uri)
    except Exception as exc:
        raise InboundAuthError(f"jwks_unavailable: {exc}") from exc

    try:
        decoded = jwt.decode(token, key_set, algorithms=["RS256"])
    except JoseError as exc:
        raise InboundAuthError(f"invalid_signature: {exc}") from exc
    except Exception as exc:
        raise InboundAuthError(f"malformed_token: {exc}") from exc

    # Validated manually (rather than via a claims-registry helper) so a
    # mismatch says exactly what was found vs. expected — the audience in
    # particular varies by how the PingOne resource is configured, and
    # guessing wrong here is the most likely first-run failure.
    claims = decoded.claims

    if claims.get("iss") != issuer:
        raise InboundAuthError(f"issuer_mismatch: token iss={claims.get('iss')!r}, expected {issuer!r}")

    exp = claims.get("exp")
    if not exp or exp < time.time():
        raise InboundAuthError(f"token_expired: exp={exp!r}")

    aud = claims.get("aud")
    aud_values = aud if isinstance(aud, list) else [aud] if aud else []
    if expected_audience not in aud_values:
        raise InboundAuthError(
            f"audience_mismatch: token aud={aud!r}, expected {expected_audience!r}"
        )

    act = claims.get("act")
    actor_sub = act.get("sub") if isinstance(act, dict) else None

    return VerifiedIdentity(
        sub=claims.get("sub"),
        client_id=claims.get("client_id"),
        actor_sub=actor_sub,
        scope=claims.get("scope"),
        email=claims.get("email") or claims.get("preferred_username"),
        aud=expected_audience,
        agent_client_id=claims.get("agent_client_id"),
    )
