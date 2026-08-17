"""Global 'latest known' snapshot of tokens minted by this service's own
outbound grant calls — populated only from real usage (app/auth/routes.py's
/agent-token endpoint, the same call the "Approve Agent Action" button
triggers), never synthesized on demand just because the Token Chain panel
was opened. Mirrors app/telemetry.py's RecordingSpanProcessor in spirit
(in-memory, single global buffer, demo-scale — not per-session, not
multi-tenant safe) but much smaller: just the two slots this service itself
mints, read by GET /api/auth/token-chain.
"""

from __future__ import annotations

from typing import Any

from app.auth.token_decode import decode_token_claims

_ledger: dict[str, dict[str, Any] | None] = {
    "agent_own": None,
    "delegation": None,
}


def record(name: str, raw_token: str) -> None:
    _ledger[name] = {"raw": raw_token, "claims": decode_token_claims(raw_token)}


def snapshot() -> dict[str, dict[str, Any] | None]:
    return dict(_ledger)
