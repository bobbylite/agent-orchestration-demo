"""PingOne OIDC: discovery (cached), PKCE authorization URL, code exchange,
and ID token verification against the tenant's JWKS.

Deliberately duplicated from backend/app/auth/oidc.py rather than shared —
same reasoning as task-agent/app/token_grants.py's duplication of
backend/app/auth/agent_auth.py: each service's own outbound grant/OIDC
calls stay independently auditable. The only real difference from
backend's copy is that `build_authorization_url`/`exchange_code_for_tokens`
already took `redirect_uri` as an explicit parameter rather than deriving
it from `Settings.app_base_url` — so this file needed no changes at all
to work with a loopback redirect URI instead of a real HTTP route.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from app.config import Settings

_discovery_cache: dict[str, dict[str, Any]] = {}
_jwks_cache: dict[str, tuple[float, Any]] = {}
_JWKS_TTL_SECONDS = 3600


@dataclass
class OidcMetadata:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    issuer: str
    end_session_endpoint: str | None = None


async def get_metadata(settings: Settings) -> OidcMetadata:
    url = settings.oidc_discovery_url
    if not url:
        raise RuntimeError("OIDC_DISCOVERY_URL is not configured")
    if url not in _discovery_cache:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            _discovery_cache[url] = resp.json()
    doc = _discovery_cache[url]
    return OidcMetadata(
        authorization_endpoint=doc["authorization_endpoint"],
        token_endpoint=doc["token_endpoint"],
        jwks_uri=doc["jwks_uri"],
        issuer=doc["issuer"],
        end_session_endpoint=doc.get("end_session_endpoint"),
    )


async def get_jwks(metadata: OidcMetadata):
    cached = _jwks_cache.get(metadata.jwks_uri)
    now = time.time()
    if cached and now - cached[0] < _JWKS_TTL_SECONDS:
        return cached[1]
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(metadata.jwks_uri)
        resp.raise_for_status()
        key_set = KeySet.import_key_set(resp.json())
    _jwks_cache[metadata.jwks_uri] = (now, key_set)
    return key_set


def build_authorization_url(
    metadata: OidcMetadata,
    settings: Settings,
    *,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{metadata.authorization_endpoint}?{urlencode(params)}"


async def exchange_code_for_tokens(
    metadata: OidcMetadata,
    settings: Settings,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            metadata.token_endpoint,
            data=data,
            auth=(settings.oidc_client_id or "", settings.oidc_client_secret or ""),
        )
        resp.raise_for_status()
        return resp.json()


async def verify_id_token(
    metadata: OidcMetadata,
    settings: Settings,
    id_token: str,
    *,
    nonce: str,
) -> dict[str, Any]:
    key_set = await get_jwks(metadata)
    token = jwt.decode(id_token, key_set, algorithms=["RS256"])
    registry = JWTClaimsRegistry(
        iss={"essential": True, "value": metadata.issuer},
        aud={"essential": True, "value": settings.oidc_client_id},
        exp={"essential": True},
        nonce={"essential": True, "value": nonce},
    )
    registry.validate(token.claims)
    return dict(token.claims)
