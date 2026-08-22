"""Session-bound CIBA approval cache, keyed by downstream capability."""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
import asyncio


@dataclass
class Approval:
    approval_id: str
    session_sub: str
    session_binding: str
    capability: str
    auth_req_id: str
    expires_at: float
    status: str = "pending"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_records: dict[str, Approval] = {}
_tokens: dict[str, dict[str, tuple[str, float]]] = {}
_ALLOWED_CAPABILITIES = {"read", "write", "delete"}
_MAX_APPROVALS = 3


def _binding(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_tokens(session_sub: str) -> dict[str, str]:
    now = time.time()
    capability_tokens = _tokens.get(session_sub, {})
    active: dict[str, str] = {}
    for capability, (token, expires_at) in list(capability_tokens.items()):
        if capability not in _ALLOWED_CAPABILITIES or expires_at <= now:
            capability_tokens.pop(capability, None)
        else:
            active[capability] = token
    return active


def store_token(session_sub: str, capability: str, token: str, expires_in: int) -> None:
    if capability not in _ALLOWED_CAPABILITIES:
        raise ValueError("unsupported CIBA capability")
    _tokens.setdefault(session_sub, {})[capability] = (token, time.time() + max(1, expires_in))


def create(*, session_sub: str, session_token: str, capability: str, auth_req_id: str, expires_in: int) -> Approval:
    if capability not in _ALLOWED_CAPABILITIES:
        raise ValueError("unsupported CIBA capability")
    get_tokens(session_sub)
    if capability not in _tokens.get(session_sub, {}) and len(_tokens.get(session_sub, {})) >= _MAX_APPROVALS:
        raise ValueError("CIBA approval limit reached")
    item = Approval(
        approval_id=secrets.token_urlsafe(32),
        session_sub=session_sub,
        session_binding=_binding(session_token),
        capability=capability,
        auth_req_id=auth_req_id,
        expires_at=time.time() + max(1, expires_in),
    )
    _records[item.approval_id] = item
    return item


def get(approval_id: str) -> Approval | None:
    item = _records.get(approval_id)
    if item and item.expires_at <= time.time():
        item.status = "expired"
    return item


def owns(item: Approval, *, session_sub: str, session_token: str, capability: str) -> bool:
    return (
        item.session_sub == session_sub
        and item.capability == capability
        and secrets.compare_digest(item.session_binding, _binding(session_token))
    )


def remove(approval_id: str) -> None:
    _records.pop(approval_id, None)


def clear_for_session(session_sub: str) -> None:
    for approval_id, item in list(_records.items()):
        if item.session_sub == session_sub:
            _records.pop(approval_id, None)
    _tokens.pop(session_sub, None)
