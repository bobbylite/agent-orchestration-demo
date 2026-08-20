"""In-memory todo store, shared by the MCP tools (app/mcp_server.py, agent
path) and the REST routes (app/routes/todos.py, human path) — one set of
data functions, two verified-identity callers. Reset on every process
restart, matching this service's existing "trusted-network, mocked" scope;
only auth and the audit log are new, not persistence.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from typing import Literal

CreatedBy = Literal["human", "agent"]

_todos: dict[str, dict] = {}
_next_id = itertools.count(1)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _seed() -> None:
    for text in ("Buy milk", "Renew passport"):
        todo_id = str(next(_next_id))
        _todos[todo_id] = {
            "id": todo_id,
            "text": text,
            "done": False,
            "created_at": _now(),
            "created_by": "human",
            "creator_sub": None,
            "creator_label": "Seed data",
            "agent_client_id": None,
        }


_seed()


def list_todos() -> list[dict]:
    return list(_todos.values())


def add_todo(
    text: str,
    *,
    created_by: CreatedBy,
    creator_sub: str | None,
    creator_label: str | None,
    agent_client_id: str | None = None,
) -> dict:
    todo_id = str(next(_next_id))
    todo = {
        "id": todo_id,
        "text": text,
        "done": False,
        "created_at": _now(),
        "created_by": created_by,
        "creator_sub": creator_sub,
        "creator_label": creator_label,
        "agent_client_id": agent_client_id,
    }
    _todos[todo_id] = todo
    return todo


def complete_todo(todo_id: str) -> dict:
    todo = _todos.get(todo_id)
    if not todo:
        raise KeyError(f"No todo with id {todo_id!r}")
    todo["done"] = True
    return todo


def reopen_todo(todo_id: str) -> dict:
    todo = _todos.get(todo_id)
    if not todo:
        raise KeyError(f"No todo with id {todo_id!r}")
    todo["done"] = False
    return todo
