"""Inbound auth for /api/invoke — thin adapter over agentorchestration_shared's
verifier, wiring this service's Settings into it. See
shared/src/agentorchestration_shared/inbound_auth.py for what's actually enforced
(signature/issuer/expiry/audience, re-checked fresh on every call — no
"session" trust) and why it's shared unmodified across services rather than
reimplemented per service.
"""

from __future__ import annotations

from agentorchestration_shared import InboundAuthError, VerifiedIdentity, verify_bearer_token

from app.config import Settings

# InboundAuthError re-exported so existing call sites
# (`from app.auth.inbound import ...`) don't need to change.
InboundIdentity = VerifiedIdentity

__all__ = ["InboundAuthError", "InboundIdentity", "verify_inbound_token"]


async def verify_inbound_token(token: str, settings: Settings) -> VerifiedIdentity:
    if not settings.oidc_discovery_url:
        raise InboundAuthError("no_discovery_url_configured")
    return await verify_bearer_token(
        token,
        discovery_url=settings.oidc_discovery_url,
        expected_audience=settings.resolved_expected_audience or "",
    )
