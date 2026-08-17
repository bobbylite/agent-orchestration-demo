"""Global 'latest known' snapshot of tokens minted by this service's own
outbound grant calls — populated only from real usage
(app/graph.py's _get_mcp_scoped_token/_scoped_tool_call, during an actual
delegated tool call), never synthesized on demand just because the Token
Chain panel was opened. Mirrors backend/app/auth/token_ledger.py's shape —
same "in-memory, single global buffer, demo-scale" convention as
app/telemetry.py's RecordingSpanProcessor.

Two mcp-scoped slots, not one — "read" and "write" are tracked
independently (keyed by which scope was actually granted) rather than one
"latest of either" slot, so the Token Chain panel can show BOTH a
read-scoped and a write-scoped MCP token side by side once each has been
used at least once. That's deliberate: collapsing them into a single
"latest" slot would hide the exact "a read succeeding never implies a write
was granted, and vice versa" story this service's step-up scoping exists to
demonstrate (see CLAUDE.md "RFC 8693 chained delegation").
"""

from __future__ import annotations

from typing import Any

from app.token_decode import decode_token_claims

_ledger: dict[str, dict[str, Any] | None] = {
    "task_agent_own": None,
    "mcp_scoped_read": None,
    "mcp_scoped_write": None,
}


def record(name: str, raw_token: str, *, tool: str | None = None) -> None:
    entry: dict[str, Any] = {"raw": raw_token, "claims": decode_token_claims(raw_token)}
    if tool:
        entry["tool"] = tool
    _ledger[name] = entry


def snapshot() -> dict[str, dict[str, Any] | None]:
    return dict(_ledger)
