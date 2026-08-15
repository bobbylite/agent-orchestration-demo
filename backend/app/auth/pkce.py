"""PKCE (S256) helpers and a short-lived signed cookie carrying {state,
nonce, code_verifier} between /api/auth/login and /api/auth/callback.

A signed (not encrypted) cookie is sufficient here: none of these values are
secret-sensitive on their own, they only matter combined with the one-time
authorization code PingOne holds server-side.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings

OIDC_STATE_COOKIE = "agentcore_oidc_state"
_STATE_MAX_AGE_SECONDS = 600


def generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def generate_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET is required to use OIDC login")
    return URLSafeTimedSerializer(settings.session_secret, salt="oidc-state")


def seal_state(payload: dict, settings: Settings) -> str:
    return _serializer(settings).dumps(payload)


def unseal_state(token: str, settings: Settings) -> dict | None:
    try:
        return _serializer(settings).loads(token, max_age=_STATE_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
