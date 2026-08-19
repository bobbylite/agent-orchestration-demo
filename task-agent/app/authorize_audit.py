"""Append-only history of PingOne Authorize decisions this service has
requested — evaluator_optimizer, task_policy, delegation_policy. Same
ring-buffer/newest-first/sequential-id shape as mcp-todos-server/app/audit.py's
OBO audit log; a genuinely different concern from telemetry.py's span
buffer (shared across every span type, capped, no per-decision identity).
"""

from __future__ import annotations

import itertools
from collections import deque
from datetime import UTC, datetime
from typing import Any, Literal

Decision = Literal["permit", "deny", "error"]

_RING_BUFFER_SIZE = 300
_entries: deque[dict[str, Any]] = deque(maxlen=_RING_BUFFER_SIZE)
_next_id = itertools.count(1)


def record(
    *,
    policy: Literal["evaluator_optimizer", "task_policy", "delegation_policy"],
    decision: Decision,
    tool: str | None = None,
    agent_client_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    entry = {
        "id": str(next(_next_id)),
        "timestamp": datetime.now(UTC).isoformat(),
        "policy": policy,
        "tool": tool,
        "decision": decision,
        "agent_client_id": agent_client_id,
        "reason": reason,
    }
    _entries.append(entry)
    return entry


def get_recent() -> list[dict[str, Any]]:
    return list(reversed(_entries))
