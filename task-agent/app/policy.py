"""Tool-access policy for delegated (agent) calls — identity only now.

Scope is no longer checked here: the inbound delegation token always
carries the same generic scope (Settings.expected_delegation_scope,
"agent:delegation") regardless of which tool is ultimately called — this
service decides which specific todos:read/todos:write capability it needs
itself, per tool call, via its own RFC 8693 Token Exchange
(app/graph.py's _scoped_tool_call). Whether that exchange actually
succeeds — and mcp-todos-server's own independent verification of the
resulting token — are the real enforcement points for *which* action is
allowed, not a local comparison here. This check only answers "is this
caller even allowed to reach this tool at all."
"""

from __future__ import annotations

from app.config import get_settings


def _identity_acl() -> dict[str, set[str]]:
    settings = get_settings()
    allowed = {settings.allowed_agent_client_id} if settings.allowed_agent_client_id else set()
    return {
        "list_todos": allowed,
        "add_todo": allowed,
        "complete_todo": allowed,
    }


def check(tool_name: str, client_id: str | None) -> bool:
    return bool(client_id) and client_id in _identity_acl().get(tool_name, set())
