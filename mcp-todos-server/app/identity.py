"""Resolves a human-readable label for a PingOne `sub`, for the OBO audit
log and for tagging agent-created todos. Two sources, tried in order (per
the confirmed design — token claim first, session-cache fallback):

1. A verified delegated token's own `email`/`preferred_username` claim
   (agentorchestration_shared.VerifiedIdentity.email) — requires the
   PingOne resource behind TODOS_READ_SCOPE/TODOS_WRITE_SCOPE to map one
   onto its access tokens; not every deployment will have this.
2. A `sub -> {email, name}` cache populated whenever a human signs into
   *this* service's own UI (app/auth/routes.py's /callback) — works
   without any extra PingOne resource config, but only for a `sub` that has
   actually signed in here at least once. Same tenant, so the same `sub`
   that shows up on a delegated token (RFC 8693 preserves the subject's
   `sub` through exchange) matches what a human's own login recorded.

If neither resolves, callers fall back to displaying the raw `sub` —
never fabricated, so the audit log never claims an identity it can't back.
"""

from __future__ import annotations

_sub_label_cache: dict[str, str] = {}


def remember_login(sub: str | None, *, email: str | None, name: str | None) -> None:
    if not sub:
        return
    label = email or name
    if label:
        _sub_label_cache[sub] = label


def resolve_label(sub: str | None, *, token_email: str | None = None) -> str | None:
    if token_email:
        return token_email
    if sub and sub in _sub_label_cache:
        return _sub_label_cache[sub]
    return None
