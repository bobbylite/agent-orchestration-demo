"""Minimal, in-memory todos MCP server (Streamable HTTP).

No auth on this server itself — it's a trusted-internal-network-only demo
service. The Task Agent that calls it is what's actually gated (inbound
auth + a policy ACL); this server just does the mocked work.
"""

from __future__ import annotations

import itertools

from fastmcp import FastMCP

mcp = FastMCP("Todos")

_todos: dict[str, dict] = {}
_next_id = itertools.count(1)


def _seed() -> None:
    for text in ("Buy milk", "Renew passport"):
        todo_id = str(next(_next_id))
        _todos[todo_id] = {"id": todo_id, "text": text, "done": False}


_seed()


@mcp.tool
def list_todos() -> list[dict]:
    """List all todos."""
    return list(_todos.values())


@mcp.tool
def add_todo(text: str) -> dict:
    """Add a new todo and return it."""
    todo_id = str(next(_next_id))
    todo = {"id": todo_id, "text": text, "done": False}
    _todos[todo_id] = todo
    return todo


@mcp.tool
def complete_todo(todo_id: str) -> dict:
    """Mark a todo as done and return it."""
    todo = _todos.get(todo_id)
    if not todo:
        raise ValueError(f"No todo with id {todo_id!r}")
    todo["done"] = True
    return todo


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=9000, path="/mcp")
