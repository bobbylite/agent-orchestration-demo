"""Unverified, display-only JWT payload decode — NOT a security check.

Every token this module touches was either (a) freshly received from
PingOne over TLS in the very call that's about to record it
(app/graph.py's _get_mcp_scoped_token/_scoped_tool_call), or (b) already
cryptographically verified elsewhere (verify_bearer_token) before this
module sees it. This exists purely so the frontend's Token Chain inspector
(GET /tokens/chain) can show real, decoded claims — it plays no role in any
auth decision. Deliberately duplicated from backend/app/auth/token_decode.py
— same "outbound-facing code isn't shared via shared/" convention as
everywhere else in this codebase.
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
