"""Bounded, safe activity history for the external AI Gateway.

The gateway is a remote service and has no local telemetry ring buffer. This
module records only what this Task Agent observed at the gateway boundary:
real tool schemas, operation outcomes, and safe failure details. It is a demo
inspector, not an authorization source of truth and never stores credentials.
"""

from __future__ import annotations

import itertools
from collections import deque
from datetime import UTC, datetime
from typing import Any, Literal

Outcome = Literal["ok", "policy_denied", "error"]
Operation = Literal["schema_discovery", "tool_call"]

_RING_BUFFER_SIZE = 100
_activities: deque[dict[str, Any]] = deque(maxlen=_RING_BUFFER_SIZE)
_tools: list[dict[str, Any]] = []
_last_discovery: dict[str, Any] | None = None
_next_id = itertools.count(1)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def record_activity(
    *,
    operation: Operation,
    outcome: Outcome,
    gateway_url: str,
    tool: str | None = None,
    http_status: int | None = None,
    reason: str | None = None,
    tool_count: int | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": str(next(_next_id)),
        "timestamp": _now(),
        "operation": operation,
        "outcome": outcome,
        "gateway_url": gateway_url,
        "tool": tool,
        "http_status": http_status,
        "reason": reason,
        "tool_count": tool_count,
    }
    _activities.append(entry)
    return entry


def record_tools(tools: list[Any], *, gateway_url: str) -> None:
    global _tools, _last_discovery
    discovered_at = _now()
    _tools = [
        {
            "name": str(tool.name),
            "description": str(getattr(tool, "description", "") or ""),
            "input_schema": getattr(tool, "args_schema", None).model_json_schema()
            if getattr(tool, "args_schema", None) is not None
            and hasattr(getattr(tool, "args_schema", None), "model_json_schema")
            else {},
            "discovered_at": discovered_at,
        }
        for tool in tools
    ]
    _last_discovery = {"timestamp": discovered_at, "outcome": "ok", "tool_count": len(_tools)}


def record_discovery_failure(*, outcome: Outcome, gateway_url: str, reason: str, http_status: int | None = None) -> None:
    global _last_discovery
    timestamp = _now()
    _last_discovery = {"timestamp": timestamp, "outcome": outcome, "reason": reason, "http_status": http_status}


def snapshot(*, gateway_url: str) -> dict[str, Any]:
    denied = sum(1 for entry in _activities if entry["outcome"] == "policy_denied")
    errors = sum(1 for entry in _activities if entry["outcome"] == "error")
    last_call = next((entry for entry in _activities if entry["operation"] == "tool_call"), None)
    return {
        "gateway_url": gateway_url,
        "tools": list(_tools),
        "activities": list(reversed(_activities)),
        "last_discovery": _last_discovery,
        "last_call": last_call,
        "summary": {
            "available_tools": len(_tools),
            "calls": sum(1 for entry in _activities if entry["operation"] == "tool_call"),
            "denied": denied,
            "errors": errors,
        },
    }


def clear() -> int:
    global _tools, _last_discovery
    count = len(_activities)
    _activities.clear()
    _tools = []
    _last_discovery = None
    return count
