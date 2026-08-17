"""Unverified, display-only JWT payload decode — NOT a security check.

Every token this module ever touches was either (a) freshly received from
PingOne over TLS in the very call that's about to record it (app/auth/routes.py),
or (b) already cryptographically verified elsewhere (verify_inbound_token)
before this module sees it. This exists purely so the Token Chain inspector
(GET /api/auth/token-chain) can show real, decoded claims to the frontend —
it plays no role in any auth decision, unlike agentorchestration_shared's
verify_bearer_token, which does full signature/issuer/audience verification.

Session cookies stay httponly and unreadable by JavaScript regardless of this
module (see app/auth/session.py) — the Token Chain endpoint is a deliberate,
explicit, read-only surface for showing decoded claims, not a weakening of
that sealing.
"""

from __future__ import annotations

import base64
import json
from typing import Any


def decode_token_claims(token: str) -> dict[str, Any]:
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}
