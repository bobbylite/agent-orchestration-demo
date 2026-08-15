"""Encrypted session cookie (JWE, A256GCM) — mirrors backend/app/auth/session.py.
Own cookie name/secret; not shared with backend/'s session, deliberately
(this is a distinct PingOne app per the confirmed design).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import Request, Response
from joserfc import jwe
from joserfc.jwk import OctKey

from app.config import Settings

SESSION_COOKIE = "mcptodos_session"

_SESSION_MAX_AGE = 8 * 60 * 60  # 8h

_HEADER = {"alg": "dir", "enc": "A256GCM"}
_ALGORITHMS = ["dir", "A256GCM"]


def _key(settings: Settings) -> OctKey:
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET is required to use encrypted sessions")
    return OctKey.import_key(hashlib.sha256(settings.session_secret.encode("utf-8")).digest())


def seal(payload: dict[str, Any], settings: Settings) -> str:
    data = json.dumps(payload).encode("utf-8")
    return jwe.encrypt_compact(_HEADER, data, _key(settings), algorithms=_ALGORITHMS)


def unseal(token: str, settings: Settings) -> dict[str, Any] | None:
    try:
        result = jwe.decrypt_compact(token, _key(settings), algorithms=_ALGORITHMS)
        return json.loads(result.plaintext)
    except Exception:
        return None


def read_cookie(request: Request, name: str, settings: Settings) -> dict[str, Any] | None:
    raw = request.cookies.get(name)
    if not raw:
        return None
    return unseal(raw, settings)


def set_sealed_cookie(
    response: Response,
    name: str,
    payload: dict[str, Any],
    settings: Settings,
    *,
    max_age: int = _SESSION_MAX_AGE,
) -> None:
    response.set_cookie(
        key=name,
        value=seal(payload, settings),
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.app_base_url.startswith("https"),
        path="/",
    )


def clear_cookie(response: Response, name: str, settings: Settings) -> None:
    response.delete_cookie(key=name, path="/", secure=settings.app_base_url.startswith("https"), samesite="lax")
