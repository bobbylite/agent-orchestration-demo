"""Server-side store for AI Gateway tokens + the silent-refresh ladder.

Why a server-side store when GATEWAY_TOKEN_COOKIE already carries tokens?
The gateway cookie is bound to ONE browser session (per-user, cleared on
logout). The refresh-token enhancement needs tokens to outlive individual
browser sessions: the server mints fresh access tokens silently from the
stored refresh token so a user (and the Task Agent's MCP calls) keeps
working across access-token expiry without a browser round-trip. The
gateway's refresh tokens are long-lived (30 days) while access tokens last
1 hour — refreshing silently is the difference between "works all month"
and "re-login every hour".

Token response shape (confirmed live 2026-09-02):

    {"access_token": "...", "refresh_token": "...", "token_type": "Bearer",
     "expires_in": 3600, "refresh_token_expires_in": 2592000,
     "scope": "openid profile email"}

In-memory, demo-scale — the same convention as token_ledger.py and
ciba_store.py. Cleared per-user on logout; a process restart drops all
tokens (users re-login once; the client registration survives, which is
the part that was expensive).

Resolution ladder in resolve_access_token() — the one entry point for
callers (invoke route, /me):

1. Stored access token still fresh → use it (zero network).
2. Expired/expiring + refresh token present → silent refresh grant;
   on success store + return the new pair.
3. Refresh failed with "unknown client" (gateway evicted the
   registration) → re-register a fresh client via gateway_dcr, then retry
   the refresh once against the new client. A refresh token issued by the
   OLD client usually dies with its registration, so this retry normally
   fails — but the gateway may honor it, and trying costs one call.
4. Everything failed → None. Caller sends the user through interactive
   gateway login (the browser flow), which now starts from a freshly
   registered client.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.auth import gateway_dcr
from app.config import Settings

logger = logging.getLogger("app.gateway_tokens")

# Refresh when the access token is within this window of expiring — chosen
# so an hour-long token is refreshed ~5 minutes before it actually dies,
# comfortably clear of clock skew on either side.
_REFRESH_SKEW_SECONDS = 300.0


class _Entry:
    __slots__ = ("access_token", "refresh_token", "expires_at", "refresh_expires_at", "scope")

    def __init__(self) -> None:
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: float = 0.0
        self.refresh_expires_at: float = 0.0
        self.scope: str | None = None


_store: dict[str, _Entry] = {}


def reset() -> None:
    """Test hook."""
    _store.clear()


def store_tokens(sub: str, token_response: dict[str, Any]) -> None:
    """Persist both tokens from a gateway token response (the
    /gateway/callback exchange, or a refresh grant). Both are kept: the
    access token for immediate use, the refresh token for silent renewal
    after it expires."""
    entry = _store.setdefault(sub, _Entry())
    entry.access_token = token_response.get("access_token")
    entry.refresh_token = token_response.get("refresh_token") or entry.refresh_token
    entry.expires_at = time.time() + int(token_response.get("expires_in", 3600))
    if token_response.get("refresh_token_expires_in") is not None:
        entry.refresh_expires_at = time.time() + int(token_response["refresh_token_expires_in"])
    entry.scope = token_response.get("scope")


def drop(sub: str) -> None:
    """Forget a user's gateway tokens (logout)."""
    _store.pop(sub, None)


def has_tokens(sub: str) -> bool:
    entry = _store.get(sub)
    return bool(entry and (entry.access_token or entry.refresh_token))


def status(sub: str) -> dict[str, Any]:
    """Diagnostic view for /me and the AI Gateway inspector — never exposes
    the tokens themselves, only their state."""
    entry = _store.get(sub)
    if not entry:
        return {"stored": False, "access_fresh": False, "refresh_available": False}
    now = time.time()
    return {
        "stored": bool(entry.access_token or entry.refresh_token),
        "access_fresh": bool(entry.access_token and now < entry.expires_at - _REFRESH_SKEW_SECONDS),
        "refresh_available": bool(entry.refresh_token and now < entry.refresh_expires_at),
    }


async def _refresh_grant(settings: Settings, client_id: str, refresh_token: str) -> dict[str, Any] | None:
    """POST the refresh_token grant. Returns the token response dict, or
    None on any failure (caller decides whether to re-register)."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            response = await client.post(settings.gateway_token_endpoint or "", data=data)
    except httpx.HTTPError as exc:
        logger.warning("Gateway refresh grant failed (network): %s", exc)
        return None
    if response.status_code >= 400:
        logger.info("Gateway refresh grant rejected (%s): %s", response.status_code, response.text[:200])
        return None
    try:
        return response.json()
    except ValueError:
        return None


async def resolve_access_token(settings: Settings, sub: str) -> str | None:
    """The ladder from the module docstring. Returns a usable gateway
    access token, or None if the user must re-login interactively.

    Never raises: every failure mode collapses to None + a log line, so
    callers treat None as 'needs interactive login' rather than a crash.
    """
    entry = _store.get(sub)
    now = time.time()

    # 1. Fresh access token — zero network.
    if entry and entry.access_token and now < entry.expires_at - _REFRESH_SKEW_SECONDS:
        return entry.access_token

    if not (entry and entry.refresh_token):
        return (entry.access_token if entry else None) or None

    if entry.refresh_expires_at and now >= entry.refresh_expires_at:
        logger.info("Gateway refresh token for sub %s has expired; interactive login needed.", sub)
        return (entry.access_token if entry and entry.access_token else None) or None

    client_id = await gateway_dcr.ensure_client_registered(settings)

    # 2. Silent refresh.
    refreshed = await _refresh_grant(settings, client_id, entry.refresh_token)
    if refreshed and refreshed.get("access_token"):
        store_tokens(sub, refreshed)
        logger.info("Silently refreshed gateway access token for sub %s.", sub)
        return refreshed["access_token"]

    # 3. Refresh rejected — if the reason looks like an evicted client,
    #    re-register and try the old refresh token once against the new
    #    client. Usually dead (refresh tokens die with their client), but
    #    cheap to attempt.
    gateway_dcr.mark_evicted()
    fresh_client_id = await gateway_dcr.ensure_client_registered(settings, force=True)
    if fresh_client_id != client_id:
        retried = await _refresh_grant(settings, fresh_client_id, entry.refresh_token)
        if retried and retried.get("access_token"):
            store_tokens(sub, retried)
            logger.info("Refresh token survived client re-registration for sub %s.", sub)
            return retried["access_token"]

    # 4. Interactive login required. Keep the (dead) refresh token in place:
    #    dropping it here would lose the only artifact that could have been
    #    presented later; the callback's store_tokens overwrites it anyway.
    return None
