"""In-memory audit log — same bounded-ring-buffer shape as
backend/app/telemetry.py's RecordingSpanProcessor, purpose-built for OBO
entries instead of OTel spans. This is the centerpiece of this service:
every tool call, human or agent, direct or delegated, gets one entry here.

Two actor shapes:
  - actor_type="human": a signed-in human acted directly via this
    service's own UI. `on_behalf_of_sub`/`on_behalf_of_label` describe
    that same human (there's no delegation, but reusing the same fields
    keeps rendering uniform).
  - actor_type="agent": an agent (task-agent/) acted using a delegated
    token. `agent_client_id`/`agent_aud` describe the agent's own verified
    identity; `on_behalf_of_sub`/`on_behalf_of_label` describe the human
    the token was issued for — this is the OBO relationship.
"""

from __future__ import annotations

import itertools
from collections import deque
from datetime import UTC, datetime
from typing import Any, Literal

Outcome = Literal["success", "denied", "error"]

_RING_BUFFER_SIZE = 300
_entries: deque[dict[str, Any]] = deque(maxlen=_RING_BUFFER_SIZE)
_next_id = itertools.count(1)


def record(
    *,
    actor_type: Literal["human", "agent"],
    tool: str,
    outcome: Outcome,
    on_behalf_of_sub: str | None,
    on_behalf_of_label: str | None,
    agent_client_id: str | None = None,
    agent_aud: str | None = None,
    agent_label: str | None = None,
    scope: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    entry = {
        "id": str(next(_next_id)),
        "timestamp": datetime.now(UTC).isoformat(),
        "actor_type": actor_type,
        "tool": tool,
        "outcome": outcome,
        "on_behalf_of_sub": on_behalf_of_sub,
        "on_behalf_of_label": on_behalf_of_label,
        "agent_client_id": agent_client_id,
        "agent_aud": agent_aud,
        "agent_label": agent_label,
        "scope": scope,
        "detail": detail,
    }
    _entries.append(entry)
    return entry


def get_recent() -> list[dict[str, Any]]:
    return list(reversed(_entries))
