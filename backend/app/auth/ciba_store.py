"""Bounded, session-bound, single-use CIBA approvals for this demo."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass, field


@dataclass
class Approval:
    approval_id: str
    session_sub: str
    session_binding: str
    auth_req_id: str
    expires_at: float
    next_poll_at: float
    interval: float
    status: str = "pending"
    token: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_records: dict[str, Approval] = {}
_MAX_RECORDS = 300


def binding(access_token: str) -> str:
    return hashlib.sha256(access_token.encode()).hexdigest()


def add(*, session_sub: str, access_token: str, auth_req_id: str, expires_in: int, interval: float) -> Approval:
    if len(_records) >= _MAX_RECORDS:
        now = time.time()
        for key, item in list(_records.items()):
            if item.expires_at <= now or item.status != "pending":
                _records.pop(key, None)
    item = Approval(
        approval_id=secrets.token_urlsafe(32),
        session_sub=session_sub,
        session_binding=binding(access_token),
        auth_req_id=auth_req_id,
        expires_at=time.time() + max(1, expires_in),
        next_poll_at=time.monotonic(),
        interval=max(0.1, interval),
    )
    _records[item.approval_id] = item
    return item


def get(approval_id: str) -> Approval | None:
    item = _records.get(approval_id)
    if item and item.status == "pending" and item.expires_at <= time.time():
        item.status = "expired"
    return item


def owns(item: Approval, *, session_sub: str, access_token: str) -> bool:
    return item.session_sub == session_sub and secrets.compare_digest(item.session_binding, binding(access_token))


def remove(approval_id: str) -> None:
    _records.pop(approval_id, None)


def clear_for_session(session_sub: str) -> None:
    for key, item in list(_records.items()):
        if item.session_sub == session_sub:
            _records.pop(key, None)
