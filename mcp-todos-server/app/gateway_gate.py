"""Session-level AI Gateway enforcement for the /mcp endpoint.

The AI Gateway is configured (Privilege console, not repo code) to validate
the opaque user-scoped credential arriving in `Authorization` and to inject
a JWT minted from a dedicated "gateway enforcement" PingOne client — one
whose tokens carry a custom claim (`gatewayEnforcement: true`) — into the
`Authorization` header on the upstream hop. This middleware verifies that
injected JWT fresh on every request (signature via the issuer's JWKS,
issuer, expiry, audience — the same shared verify_bearer_token() used for
the delegated token) and additionally requires the enforcement claim.

This gates the /mcp endpoint at the HTTP level, so `initialize` and
`tools/list` are covered — not just the per-tool-call checks in
app/mcp_server.py, which remain unchanged. A caller that bypasses the
gateway (direct mode) carries no such JWT and is rejected here, including
from the tool list.

CLAUDE.md's core rule applies: this is a deterministic gate, fail-CLOSED
(opposite direction from the task-agent judge's fail-open). It runs as a
plain ASGI middleware before any MCP session handling — never as a graph
node, never influenced by model output.

Pure ASGI, not BaseHTTPMiddleware: the MCP streamable transport's GET/SSE
stream must pass through unbuffered, and BaseHTTPMiddleware would break it.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from agentorchestration_shared import InboundAuthError, verify_bearer_token

from app.config import get_settings
from app.telemetry import with_span

logger = logging.getLogger(__name__)

# ASGI callables, spelled out for clarity of the middleware contract.
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]


def _header_value(scope: dict, name: str) -> str:
    """Read a header from the ASGI scope (keys are lowercase bytes)."""
    target = name.encode("latin-1")
    for key, value in scope.get("headers", []):
        if key == target:
            return value.decode("latin-1").strip()
    return ""


def _denied_response(reason: str) -> dict:
    return {
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"www-authenticate", b'Bearer realm="mcp", error="invalid_token"'),
        ],
    }, {
        "type": "http.response.body",
        "body": (
            '{"error":"gateway_enforcement_failed","reason":"%s"}' % reason.replace('"', "'")
        ).encode("utf-8"),
    }


class GatewayEnforcementMiddleware:
    """Verifies the gateway-injected enforcement JWT on /mcp (fail closed).

    Passes through everything that isn't an enforced /mcp request: the
    human-UI routes (/api/*), health, /telemetry, CORS preflights (OPTIONS
    carries no Authorization by spec), lifespans, and — when
    MCP_GATEWAY_ENFORCEMENT=false — every /mcp request (direct mode).
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")
        settings = get_settings()
        if path != "/mcp" or method == "OPTIONS" or not settings.mcp_gateway_enforcement:
            await self.app(scope, receive, send)
            return

        with with_span("gateway.enforcement", {"gateway.path": path, "http.method": method}) as span:
            token = _extract_bearer(scope)
            if not token:
                span.set_attribute("gateway.outcome", "denied")
                span.set_attribute("gateway.reason", "missing_bearer_token")
                logger.warning("Gateway enforcement denied /mcp: missing bearer token")
                await _send_denied(send, "missing_bearer_token")
                return

            try:
                identity = await verify_bearer_token(
                    token,
                    discovery_url=settings.oidc_discovery_url or "",
                    expected_audience=settings.agent_expected_audience or "",
                )
            except InboundAuthError as exc:
                span.set_attribute("gateway.outcome", "denied")
                span.set_attribute("gateway.reason", exc.reason)
                logger.warning("Gateway enforcement rejected /mcp bearer: %s", exc.reason)
                await _send_denied(send, exc.reason)
                return

            claim_name = settings.gateway_enforcement_claim
            claim_value = identity.claims.get(claim_name)
            if claim_value is not True:
                span.set_attribute("gateway.outcome", "denied")
                span.set_attribute("gateway.reason", f"missing_or_false_claim:{claim_name}")
                span.set_attribute("gateway.claim_value", repr(claim_value))
                logger.warning(
                    "Gateway enforcement rejected /mcp bearer: %s=%r", claim_name, claim_value
                )
                await _send_denied(send, f"{claim_name} claim missing or not true")
                return

            span.set_attribute("gateway.outcome", "ok")
            await self.app(scope, receive, send)


def _extract_bearer(scope: dict) -> str:
    header = _header_value(scope, "authorization")
    if not header:
        return ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


async def _send_denied(send: Send, reason: str) -> None:
    start, body = _denied_response(reason)
    await send(start)
    await send(body)
