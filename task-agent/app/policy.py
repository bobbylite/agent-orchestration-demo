"""Tool-access policy: which agent client_ids may invoke which mocked MCP
tools, AND which delegation scope the caller's verified token must actually
carry to do so. A stand-in for a real policy service — deliberately a plain
function, not something an LLM reasons about (see CLAUDE.md's core rule:
authorization never lives inside the graph or an LLM's judgment).

Two independent checks, both must pass:
  1. identity — is this agent client_id even allowed to touch this tool
  2. scope — does its verified token carry the scope this tool needs

The scope check is what makes per-action approval (todos:read vs
todos:write) actually mean something, rather than being a UI label with no
enforcement behind it — a token approved only for reading cannot be used to
complete a todo, regardless of which agent presents it.
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


def _required_scope() -> dict[str, str]:
    settings = get_settings()
    return {
        "list_todos": settings.todos_read_scope,
        "add_todo": settings.todos_write_scope,
        "complete_todo": settings.todos_write_scope,
    }


def check(tool_name: str, client_id: str | None, granted_scope: str | None) -> bool:
    if not client_id or client_id not in _identity_acl().get(tool_name, set()):
        return False
    required = _required_scope().get(tool_name)
    if required is None:
        return True
    return required in (granted_scope or "").split()
