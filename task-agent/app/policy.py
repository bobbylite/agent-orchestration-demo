"""Hardcoded tool-access policy: which agent client_ids may invoke which
mocked MCP tools. A stand-in for a real policy service — deliberately a
plain dict lookup, not something an LLM reasons about (see CLAUDE.md's core
rule: authorization never lives inside the graph or an LLM's judgment).

`complete_todo` is deliberately left with no allowed callers below, so this
ACL demonstrates a real denial, not just a rubber stamp.
"""

from __future__ import annotations

from app.config import get_settings


def _acl() -> dict[str, set[str]]:
    settings = get_settings()
    allowed = {settings.allowed_agent_client_id} if settings.allowed_agent_client_id else set()
    return {
        "list_todos": allowed,
        "add_todo": allowed,
        "complete_todo": set(),
    }


def check(tool_name: str, client_id: str | None) -> bool:
    if not client_id:
        return False
    return client_id in _acl().get(tool_name, set())
