"""PKCE (S256) helpers — duplicated from backend/app/auth/pkce.py, minus
the cookie-sealing half. backend needs seal_state/unseal_state because its
PKCE state (verifier/state/nonce) has to survive a redirect through the
user's own browser between two separate HTTP requests (/login then
/callback) to a stateless FastAPI process. This bridge doesn't have that
problem: app/local_login.py generates the verifier/state/nonce and
receives the callback within the same async function call, in the same
process — the values just live in local variables for the few seconds the
flow takes, nothing needs to be sealed into anything.
"""

from __future__ import annotations

import base64
import hashlib
import secrets


def generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def generate_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)
