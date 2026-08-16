"""Caches the two credentials this bridge needs — a real PingOne user
sign-in, and the delegation credential minted from it — so a browser
doesn't pop up on every single tool call.

Same two-step chain backend/app/auth/routes.py's POST /api/auth/agent-
token performs (Client Credentials, then RFC 8693 Token Exchange), just
triggered by the first tool call instead of a UI button. The user sign-in
(app/local_login.py, a real browser prompt) is the expensive/rare
refresh; the delegation credential built from it is refreshed far more
often but never needs a new browser prompt on its own — same "step 1 is
rare, step 2 is cheap and frequent" shape the rest of this app already
has.
"""

from __future__ import annotations

import logging
import time

from app import local_login, oidc
from app.agent_auth import client_credentials_grant, token_exchange
from app.config import Settings

logger = logging.getLogger(__name__)

# Refresh a cached token this long before its actual expiry, so a call in
# flight doesn't race the token expiring mid-request.
_EXPIRY_SAFETY_MARGIN_SECONDS = 30


class CredentialManager:
    """One instance per server process — holds whatever credentials this
    process has obtained so far. There's no persistence across process
    restarts (Claude Desktop respawning this server means a fresh browser
    prompt) — see CLAUDE.md for why that's a deliberate simplicity choice,
    not an oversight."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._user_token: str | None = None
        self._user_token_expires_at: float = 0.0
        self._delegation_token: str | None = None
        self._delegation_token_expires_at: float = 0.0

    async def _ensure_user_token(self) -> str:
        if self._user_token and time.time() < self._user_token_expires_at:
            return self._user_token

        tokens = await local_login.sign_in(self._settings)
        self._user_token = tokens["access_token"]
        self._user_token_expires_at = (
            time.time() + tokens.get("expires_in", 3600) - _EXPIRY_SAFETY_MARGIN_SECONDS
        )
        # A fresh user token invalidates any delegation token minted from
        # the old one — force the next get_delegation_token() call to
        # re-derive it (cheap: no new browser prompt, just steps 2-3 again).
        self._delegation_token = None
        return self._user_token

    async def get_delegation_token(self) -> str:
        if self._delegation_token and time.time() < self._delegation_token_expires_at:
            return self._delegation_token

        user_token = await self._ensure_user_token()
        metadata = await oidc.get_metadata(self._settings)

        logger.info(
            "Proving this bridge's own identity (Client Credentials, scope=%s)", self._settings.agent_own_scope
        )
        actor = await client_credentials_grant(
            metadata.token_endpoint,
            client_id=self._settings.agent_client_id,
            client_secret=self._settings.agent_client_secret,
            scope=self._settings.agent_own_scope,
        )

        logger.info(
            "Minting a delegation credential for the Task Agent (Token Exchange, scope=%s)",
            self._settings.agent_delegation_scope,
        )
        delegated = await token_exchange(
            metadata.token_endpoint,
            client_id=self._settings.agent_delegation_client_id,
            client_secret=self._settings.agent_delegation_client_secret,
            subject_token=user_token,
            actor_token=actor["access_token"],
            scope=self._settings.agent_delegation_scope,
        )
        self._delegation_token = delegated["access_token"]
        self._delegation_token_expires_at = (
            time.time() + delegated.get("expires_in", 300) - _EXPIRY_SAFETY_MARGIN_SECONDS
        )
        return self._delegation_token
