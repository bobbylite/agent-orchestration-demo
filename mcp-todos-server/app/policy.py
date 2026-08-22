"""Tool-access policy for delegated (agent) calls — mirrors
task-agent/app/policy.py's shape exactly (see CLAUDE.md: "copy that shape,
don't reinvent it"). Checked independently of task-agent's own gate: this
service doesn't trust that task-agent already enforced this, the same way
task-agent doesn't trust the Chat Agent's own tool availability. Two
checks, both must pass:

  1. identity — is this client_id even allowed to touch this tool
  2. scope — does its verified token carry the scope this tool needs
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
        "reopen_todo": allowed,
        "delete_todo": allowed,
    }


def _required_scope() -> dict[str, str]:
    settings = get_settings()
    return {
        "list_todos": settings.todos_read_scope,
        "add_todo": settings.todos_write_scope,
        "complete_todo": settings.todos_write_scope,
        "reopen_todo": settings.todos_write_scope,
        "delete_todo": settings.todos_delete_scope,
    }


def check(tool_name: str, client_id: str | None, granted_scope: str | None) -> bool:
    if not client_id or client_id not in _identity_acl().get(tool_name, set()):
        return False
    required = _required_scope().get(tool_name)
    if required is None:
        return True
    return required in (granted_scope or "").split()
