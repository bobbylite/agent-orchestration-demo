"""Todos REST API for this service's own UI — session-cookie authenticated,
same "a verified session is sufficient for a non-delegated action" tier
the chat app already uses for plain chat (see CLAUDE.md's "core
architectural decision" — two privilege tiers, not one blanket gate).
Calls the same app/store.py functions the MCP tools use, tagged
created_by="human".

Mutations (add/complete) write an audit entry — those are meaningful
"someone did something" events. A plain list read of your own data is not:
it's not a delegated (OBO) action and carries no security signal beyond
"you looked at your own list", so list_todos_route deliberately does NOT
audit-log — the frontend polls this route, and logging every poll tick
would flood the audit log with noise that drowns out the OBO entries the
log actually exists to surface.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import audit, store
from app.auth.session import SESSION_COOKIE, read_cookie
from app.config import Settings, get_settings

router = APIRouter(prefix="/api/todos", tags=["todos"])


class AddTodoRequest(BaseModel):
    text: str


def _require_session(request: Request, settings: Settings) -> dict:
    session = read_cookie(request, SESSION_COOKIE, settings)
    if not session:
        raise HTTPException(status_code=401, detail="Sign in with PingOne first")
    return session


@router.get("")
async def list_todos_route(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    _require_session(request, settings)
    return {"todos": store.list_todos()}


@router.post("")
async def add_todo_route(
    request: Request, body: AddTodoRequest, settings: Settings = Depends(get_settings)
) -> dict:
    session = _require_session(request, settings)
    label = session.get("email") or session.get("name")
    todo = store.add_todo(
        body.text,
        created_by="human",
        creator_sub=session.get("sub"),
        creator_label=label,
    )
    audit.record(
        actor_type="human",
        tool="add_todo",
        outcome="success",
        on_behalf_of_sub=session.get("sub"),
        on_behalf_of_label=label,
    )
    return todo


@router.post("/{todo_id}/reopen")
async def reopen_todo_route(
    request: Request, todo_id: str, settings: Settings = Depends(get_settings)
) -> dict:
    session = _require_session(request, settings)
    label = session.get("email") or session.get("name")
    try:
        todo = store.reopen_todo(todo_id)
    except KeyError as exc:
        audit.record(
            actor_type="human",
            tool="reopen_todo",
            outcome="error",
            on_behalf_of_sub=session.get("sub"),
            on_behalf_of_label=label,
            detail=str(exc),
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(
        actor_type="human",
        tool="reopen_todo",
        outcome="success",
        on_behalf_of_sub=session.get("sub"),
        on_behalf_of_label=label,
    )
    return todo


@router.delete("/{todo_id}")
async def delete_todo_route(
    request: Request, todo_id: str, settings: Settings = Depends(get_settings)
) -> dict:
    session = _require_session(request, settings)
    label = session.get("email") or session.get("name")
    try:
        todo = store.delete_todo(todo_id)
    except KeyError as exc:
        audit.record(
            actor_type="human",
            tool="delete_todo",
            outcome="error",
            on_behalf_of_sub=session.get("sub"),
            on_behalf_of_label=label,
            detail=str(exc),
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(
        actor_type="human",
        tool="delete_todo",
        outcome="success",
        on_behalf_of_sub=session.get("sub"),
        on_behalf_of_label=label,
    )
    return todo


@router.post("/{todo_id}/complete")
async def complete_todo_route(
    request: Request, todo_id: str, settings: Settings = Depends(get_settings)
) -> dict:
    session = _require_session(request, settings)
    label = session.get("email") or session.get("name")
    try:
        todo = store.complete_todo(todo_id)
    except KeyError as exc:
        audit.record(
            actor_type="human",
            tool="complete_todo",
            outcome="error",
            on_behalf_of_sub=session.get("sub"),
            on_behalf_of_label=label,
            detail=str(exc),
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(
        actor_type="human",
        tool="complete_todo",
        outcome="success",
        on_behalf_of_sub=session.get("sub"),
        on_behalf_of_label=label,
    )
    return todo
