"""One-time-per-process browser sign-in — Authorization Code + PKCE with a
loopback redirect, the same pattern real CLI tools (`gh auth login`,
`ant auth login`) use. Stands in for backend/'s browser-based
/api/auth/login + /api/auth/callback routes, which rely on a cookie set on
the first request and read back on the second — this local stdio process
has no browser-to-process cookie jar to share, so the whole PKCE exchange
happens inside one function call instead of across two HTTP routes.
"""

from __future__ import annotations

import asyncio
import http.server
import logging
import threading
import webbrowser
from typing import Any
from urllib.parse import parse_qs, urlparse

from app import oidc
from app.config import Settings
from app.pkce import generate_pkce_pair, generate_token

logger = logging.getLogger(__name__)


class _CallbackServer(http.server.HTTPServer):
    result: dict[str, str | None] | None = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:  # noqa: N802 — stdlib method name
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = parse_qs(parsed.query)
        self.server.result = {
            "code": params.get("code", [None])[0],
            "state": params.get("state", [None])[0],
            "error": params.get("error", [None])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body>Signed in with PingOne \xe2\x80\x94 you can close this tab "
            b"and return to Claude Desktop.</body></html>"
        )
        # shutdown() from inside the request thread would deadlock (it waits
        # for serve_forever's loop, which is what's calling us) — hand it to
        # a throwaway thread instead.
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — silence default request logging
        pass


async def _wait_for_callback(port: int) -> dict[str, str | None] | None:
    server = _CallbackServer(("127.0.0.1", port), _CallbackHandler)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, server.serve_forever)
    return server.result


async def sign_in(settings: Settings) -> dict[str, Any]:
    """Opens a browser for a real PingOne Authorization Code + PKCE flow,
    catches the redirect on a local loopback listener, and returns the
    token response dict (access_token, id_token, expires_in, ...) plus the
    verified ID token's claims under "claims". Raises RuntimeError on any
    failure — callers should surface that as "check PingOne app #1's
    redirect URIs include http://localhost:<port>/callback and try again".
    """
    metadata = await oidc.get_metadata(settings)
    verifier, challenge = generate_pkce_pair()
    state = generate_token()
    nonce = generate_token()
    redirect_uri = f"http://localhost:{settings.local_callback_port}/callback"

    authorize_url = oidc.build_authorization_url(
        metadata,
        settings,
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
        code_challenge=challenge,
    )
    logger.info("Opening a browser for PingOne sign-in — waiting on http://localhost:%d/callback", settings.local_callback_port)
    if not webbrowser.open(authorize_url):
        logger.warning("Could not auto-open a browser. Visit this URL to sign in: %s", authorize_url)

    result = await _wait_for_callback(settings.local_callback_port)
    if not result or result.get("error") or not result.get("code"):
        raise RuntimeError(
            f"PingOne sign-in failed or was cancelled ({result}). Confirm PingOne app #1 has "
            f"redirect URI http://localhost:{settings.local_callback_port}/callback registered."
        )
    if result.get("state") != state:
        raise RuntimeError("PingOne sign-in failed: state mismatch (possible CSRF or a stale callback).")

    tokens = await oidc.exchange_code_for_tokens(
        metadata,
        settings,
        code=result["code"],  # type: ignore[arg-type]
        redirect_uri=redirect_uri,
        code_verifier=verifier,
    )
    claims = await oidc.verify_id_token(metadata, settings, tokens["id_token"], nonce=nonce)
    logger.info("Signed in as %s (sub=%s)", claims.get("email") or claims.get("name") or "unknown", claims.get("sub"))
    return {**tokens, "claims": claims}
