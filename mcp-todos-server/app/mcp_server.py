"""The MCP tool surface (Streamable HTTP) — list_todos/add_todo/
complete_todo, called by task-agent/ on an agent's behalf.

Every tool independently verifies the caller's bearer token fresh (same
agentorchestration_shared.verify_bearer_token() used everywhere else in
this repo) and independently re-checks policy — this service doesn't trust
that task-agent already gated the call, the same way task-agent doesn't
trust the Chat Agent's own tool availability. See CLAUDE.md's core rule:
auth is a plain check before the work happens, never something the model
or an upstream service's say-so is trusted for.

Every call — allowed or denied — is written to the audit log
(app/audit.py) attributing it to the real human the token was issued for
(OBO), not just the calling agent's own identity.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request

from agentorchestration_shared import InboundAuthError, VerifiedIdentity, verify_bearer_token

from app import audit, authorize, identity, policy, store
from app.telemetry import with_span
from app.config import get_settings

mcp = FastMCP("Todos")


def _call_bearer_token() -> str:
    """Read the delegated MCP caller token from the header the current auth
    mode selects (app/config.py's mcp_auth_downstream_header):

    - true (default, gateway mode): the token rides
      X-MCP-Downstream-Authorization — the outer Authorization header is the
      gateway-injected enforcement JWT (app/gateway_gate.py verifies it
      session-level when MCP_GATEWAY_ENFORCEMENT is on), so it must not be
      mistaken for the delegated token, which is verified here per tool call.
    - false (direct mode): the standard Authorization header carries the
      delegated token (callers that bypass the gateway).

    The error reasons are prefixed downstream_/standard_ respectively so a
    misconfigured mode is visible in the ToolError text.
    """
    request = get_http_request()
    settings = get_settings()
    if settings.mcp_auth_downstream_header:
        header = request.headers.get("x-mcp-downstream-authorization", "").strip()
        missing, invalid = "missing_downstream_bearer_token", "invalid_downstream_bearer_token"
    else:
        header = request.headers.get("authorization", "").strip()
        missing, invalid = "missing_standard_bearer_token", "invalid_standard_bearer_token"
    if not header:
        raise InboundAuthError(missing)
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise InboundAuthError(invalid)
    return token.strip()


async def _verify_caller() -> VerifiedIdentity:
    settings = get_settings()
    token = _call_bearer_token()
    return await verify_bearer_token(
        token,
        discovery_url=settings.oidc_discovery_url or "",
        expected_audience=settings.agent_expected_audience or "",
    )


def _record(tool: str, outcome: audit.Outcome, caller: VerifiedIdentity, *, detail: str | None = None) -> str | None:
    label = identity.resolve_label(caller.sub, token_email=caller.email)
    audit.record(
        actor_type="agent",
        tool=tool,
        outcome=outcome,
        on_behalf_of_sub=caller.sub,
        on_behalf_of_label=label,
        # `agent_client_id` (PingOne's custom claim, propagated from the
        # actor token used in the exchange that produced this token) is
        # "which agent is delegating" (task-agent's own identity) —
        # `caller.client_id` is just whichever app performed THIS specific
        # exchange call (TODOS_MCP_CLIENT_ID), not a meaningful agent
        # identity for the audit log. Confirmed via a real PingOne token
        # 2026-08-16; same distinction policy.check() below uses.
        agent_client_id=caller.agent_client_id,
        agent_aud=caller.aud,
        agent_label=get_settings().agent_display_name,
        scope=caller.scope,
        detail=detail,
    )
    return label


async def _authorize(tool_name: str) -> VerifiedIdentity:
    try:
        caller = await _verify_caller()
    except InboundAuthError as exc:
        raise ToolError(f"Inbound auth rejected the downstream token: {exc.reason}") from exc

    if not policy.check(tool_name, caller.agent_client_id, caller.scope):
        detail = (
            f"agent '{caller.agent_client_id}' (granted scope: {caller.scope!r}) is not authorized to use '{tool_name}'"
        )
        _record(tool_name, "denied", caller, detail=detail)
        raise ToolError(detail)

    with with_span("authorize.mcp_tool_call", {"policy.tool": tool_name}) as span:
        permitted, reason = await authorize.check_tool_call(
            get_settings(), access_token=_call_bearer_token(), tool_name=tool_name
        )
        span.set_attribute("policy.result", "permit" if permitted else "deny")
        if not permitted:
            detail = f"MCP Authorize PDP denied '{tool_name}': {reason}"
            _record(tool_name, "denied", caller, detail=detail)
            raise ToolError(detail)

    with with_span("mcp.tool_call", {"mcp.tool": tool_name}) as span:
        span.set_attribute("mcp.outcome", "authorized")
    return caller


@mcp.tool
async def list_todos() -> list[dict]:
    """List all todos."""
    caller = await _authorize("list_todos")
    _record("list_todos", "success", caller)
    return store.list_todos()


@mcp.tool
async def add_todo(text: str) -> dict:
    """Add a new todo and return it."""
    caller = await _authorize("add_todo")
    label = _record("add_todo", "success", caller)
    return store.add_todo(
        text,
        created_by="agent",
        creator_sub=caller.sub,
        creator_label=label,
        agent_client_id=caller.agent_client_id,
    )


@mcp.tool
async def complete_todo(todo_id: str) -> dict:
    """Mark a todo as done and return it."""
    caller = await _authorize("complete_todo")
    try:
        todo = store.complete_todo(todo_id)
    except KeyError as exc:
        _record("complete_todo", "error", caller, detail=str(exc))
        raise ToolError(str(exc)) from exc
    _record("complete_todo", "success", caller)
    return todo


@mcp.tool
async def reopen_todo(todo_id: str) -> dict:
    """Reopen a completed todo and return it."""
    caller = await _authorize("reopen_todo")
    try:
        todo = store.reopen_todo(todo_id)
    except KeyError as exc:
        _record("reopen_todo", "error", caller, detail=str(exc))
        raise ToolError(str(exc)) from exc
    _record("reopen_todo", "success", caller)
    return todo


@mcp.tool
async def delete_todo(todo_id: str) -> dict:
    """Delete a todo permanently and return the deleted object."""
    caller = await _authorize("delete_todo")
    try:
        todo = store.delete_todo(todo_id)
    except KeyError as exc:
        _record("delete_todo", "error", caller, detail=str(exc))
        raise ToolError(str(exc)) from exc
    _record("delete_todo", "success", caller)
    return todo
