"""Inbound auth for /api/invoke — verifies the bearer token the same way
AWS Bedrock AgentCore's inbound auth (a JWT authorizer) would: signature
against the issuer's JWKS, issuer, expiry, and audience, checked fresh on
every call. There is no notion of a "session" here — a token that's merely
sealed in one of our own cookies is not itself sufficient; it still has to
independently verify, and it must be a token whose audience is this agent
specifically (i.e. one that came out of RFC 8693 token exchange), not the
user's raw OIDC session token, which was never scoped to the agent at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from joserfc import jwt
from joserfc.errors import JoseError

from app.auth.oidc import get_jwks, get_metadata
from app.config import Settings


class InboundAuthError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class InboundIdentity:
    sub: str | None
    client_id: str | None
    actor_sub: str | None  # RFC 8693 `act.sub`, if the issuer populates it


async def verify_inbound_token(token: str, settings: Settings) -> InboundIdentity:
    if not settings.resolved_expected_audience:
        raise InboundAuthError("no_expected_audience_configured")

    metadata = await get_metadata(settings)

    try:
        key_set = await get_jwks(metadata)
    except Exception as exc:
        raise InboundAuthError(f"jwks_unavailable: {exc}") from exc

    try:
        decoded = jwt.decode(token, key_set, algorithms=["RS256"])
    except JoseError as exc:
        raise InboundAuthError(f"invalid_signature: {exc}") from exc
    except Exception as exc:
        raise InboundAuthError(f"malformed_token: {exc}") from exc

    # Validated manually (rather than via joserfc's JWTClaimsRegistry) so a
    # mismatch says exactly what was found vs. expected — the audience in
    # particular varies by how the PingOne resource is configured, and
    # guessing wrong here is the most likely first-run failure.
    claims = decoded.claims

    if claims.get("iss") != metadata.issuer:
        raise InboundAuthError(f"issuer_mismatch: token iss={claims.get('iss')!r}, expected {metadata.issuer!r}")

    exp = claims.get("exp")
    if not exp or exp < time.time():
        raise InboundAuthError(f"token_expired: exp={exp!r}")

    aud = claims.get("aud")
    aud_values = aud if isinstance(aud, list) else [aud] if aud else []
    if settings.resolved_expected_audience not in aud_values:
        raise InboundAuthError(
            f"audience_mismatch: token aud={aud!r}, expected {settings.resolved_expected_audience!r} "
            "(set AGENT_EXPECTED_AUDIENCE to the value shown in token aud)"
        )

    act = claims.get("act")
    actor_sub = act.get("sub") if isinstance(act, dict) else None

    return InboundIdentity(
        sub=claims.get("sub"),
        client_id=claims.get("client_id"),
        actor_sub=actor_sub,
    )
