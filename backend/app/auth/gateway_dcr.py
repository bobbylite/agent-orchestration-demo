"""Dynamic Client Registration (RFC 7591) against the AI Gateway.

The gateway forgets its dynamically-registered OAuth clients on its own
internal schedule — NOT tied to gateway restarts (confirmed live
2026-09-01: the gateway pod had 0 restarts across 5+ days while a
previously working client_id came back `400 Unknown client` from
/authorize; the gateway's own log:
`[mcpgw] authorize for unregistered client_id "..."; rejecting`). The
registration table lives in the gateway process's memory with an
undocumented eviction policy.

Model (since refresh-token support was enabled on the gateway, 2026-09-02):
- This service registers ONE client at startup and keeps it for the
  process's entire lifetime. The client is minted lazily on first gateway
  use, then pinned in memory — .env/helm's GATEWAY_CLIENT_ID is only a
  bootstrap seed, never mutated on disk.
- When the gateway reports "Unknown client" for our client, the recovery
  ladder is: try a silent refresh-token grant first (a live refresh token
  may still be accepted), then re-register if refresh fails too, and only
  fall back to an interactive browser re-login when there is no refresh
  token at all.
- All gateway network calls here use `verify=False` because the gateway
  presents a self-signed cert in this environment (same reason the
  gateway-client-reregistration skill's curl uses -k). Every other gateway
  call in this app goes through the user's browser or an authorized
  session, not this path.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote

import httpx

from app.config import Settings

logger = logging.getLogger("app.gateway_dcr")

DEFAULT_REGISTRATION_ENDPOINT = (
    "https://rluisi-agentless-mcpgw.ping-devops.com/todos-mcp-server/register"
)

# Payload the gateway expects — redirect URIs cover BOTH the local backend
# (http://localhost:8000) and the deployed console, so one registered client
# works in both environments. token_endpoint_auth_method "none" = public
# client, PKCE only, no secret to rotate or store. grant_types includes
# refresh_token (enabled on the gateway 2026-09-02 — the token response now
# carries a 30-day refresh token alongside the 1-hour access token).
REGISTRATION_PAYLOAD = {
    "client_name": "Agent Orchestration Console",
    "client_uri": "https://rluisi-agent-orchestration-client.ping-devops.com",
    "redirect_uris": [
        "http://localhost:8000/api/auth/gateway/callback",
        "https://rluisi-agent-orchestration-client.ping-devops.com/api/auth/gateway/callback",
    ],
    "token_endpoint_auth_method": "none",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
}

# Throwaway PKCE challenge used only for liveness probes — the probe request
# is rejected or redirected before any token is issued, so this is not a
# verifier for any session (same constant the diagnosis skill uses).
_PROBE_CHALLENGE = "v0ZaQK2AWE1EDhGp2Ak9uQzS14zvjPHd6JAhXcTnC04"


class GatewayRegistrationError(RuntimeError):
    """Raised when the gateway's /register or /authorize probe fails in a
    way that is not the known 'Unknown client' self-heal case."""


class _ClientState:
    """The single client registration this process owns. Registered lazily
    (first gateway use, or startup if configured) and kept for the
    process's entire lifetime — re-registration only happens when the
    gateway reports the current one as unknown AND the refresh ladder
    failed."""

    __slots__ = ("client_id", "registered_at", "evicted")

    def __init__(self) -> None:
        self.client_id: str | None = None
        self.registered_at: float = 0.0
        self.evicted: bool = False


_state = _ClientState()


def current_client_id() -> str | None:
    """The client_id this process is currently using, if one has been
    registered/seeded. Read-only view for diagnostics/routes."""
    return _state.client_id


def mark_evicted() -> None:
    """Flag the current client as evicted (an 'unknown client' response was
    seen). The next resolve_client_id() call will attempt the recovery
    ladder instead of returning the flagged id."""
    _state.evicted = True


def reset() -> None:
    """Test hook — forget the registration entirely."""
    global _state
    _state = _ClientState()


def _registration_endpoint(settings: Settings) -> str:
    return settings.gateway_registration_endpoint or DEFAULT_REGISTRATION_ENDPOINT


async def register_client(settings: Settings) -> dict:
    """POST /register (RFC 7591). Always mints a NEW client — the gateway
    does not upsert by client_name, and there is no RFC 7592 management
    endpoint to update an existing registration in place (the discovery
    doc exposes only authorize/token/register)."""
    endpoint = _registration_endpoint(settings)
    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        response = await client.post(
            endpoint,
            json=REGISTRATION_PAYLOAD,
            headers={"Accept": "application/json"},
        )
    if response.status_code >= 400:
        raise GatewayRegistrationError(
            f"Gateway DCR registration failed ({response.status_code}): {response.text[:300]}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise GatewayRegistrationError(
            f"Gateway DCR returned non-JSON: {response.text[:300]}"
        ) from exc
    if not body.get("client_id"):
        raise GatewayRegistrationError(f"Gateway DCR response missing client_id: {body}")
    return body


async def probe_client_id(settings: Settings, client_id: str) -> bool:
    """True if the gateway currently recognizes this client_id.

    GET /authorize with a syntactically-valid but throwaway PKCE challenge:
    - 302/303 → the gateway accepted the client (a real browser would be
      redirected to its upstream OIDC login; we take the status as the
      signal and never follow it).
    - 400 + "unknown client" in body → registration evicted.
    - Anything else → logged, treated as NOT ok (a client that can't
      authorize is not usable, regardless of cause).
    """
    authorize_endpoint = settings.gateway_authorization_endpoint
    if not authorize_endpoint or not client_id:
        return False
    redirect_uri = REGISTRATION_PAYLOAD["redirect_uris"][0]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": quote(redirect_uri, safe=""),
        "scope": settings.gateway_scopes,
        "state": "probe",
        "code_challenge": _PROBE_CHALLENGE,
        "code_challenge_method": "S256",
    }
    query = "&".join(f"{key}={value}" for key, value in params.items())
    try:
        async with httpx.AsyncClient(timeout=10, verify=False, follow_redirects=False) as client:
            response = await client.get(f"{authorize_endpoint}?{query}")
    except httpx.HTTPError as exc:
        logger.warning("Gateway client probe failed (network): %s", exc)
        return False
    if response.status_code in (302, 303):
        return True
    if response.status_code == 400 and "unknown client" in response.text.lower():
        return False
    logger.warning(
        "Gateway client probe inconclusive (%s): %s",
        response.status_code,
        response.text[:200],
    )
    return False


async def ensure_client_registered(settings: Settings, *, force: bool = False) -> str:
    """Return the client_id this process should use for gateway flows.

    - First call in the process lifetime registers a fresh client (the
      configured GATEWAY_CLIENT_ID seed is deliberately NOT probed/reused —
      the gateway may have forgotten it, and a process-lifetime client
      should be one this process itself minted).
    - Subsequent calls return the same id with zero network cost unless the
      client has been flagged evicted (mark_evicted) and the caller forces
      recovery (force=True), in which case a fresh client is registered.
    """
    if _state.client_id and not _state.evicted and not force:
        return _state.client_id
    if _state.client_id and _state.evicted and not force:
        # Evicted but recovery not requested (e.g. a diagnostic read) —
        # still return the id; only token-resolution paths force recovery.
        return _state.client_id

    registration = await register_client(settings)
    new_client_id = str(registration["client_id"])
    previous = _state.client_id
    _state.client_id = new_client_id
    _state.registered_at = time.time()
    _state.evicted = False
    logger.warning(
        "Registered new AI Gateway OAuth client %s (replacing %s).",
        new_client_id,
        previous or "nothing",
    )
    return new_client_id
