from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from opentelemetry.trace import Status, StatusCode
from sse_starlette.sse import EventSourceResponse

from app.agent.tools import NEEDS_AGENT_AUTH_MARKER
from app.auth.inbound import InboundAuthError, verify_inbound_token
from app.auth.session import EXCHANGED_TOKEN_COOKIE, SESSION_COOKIE, read_cookie
from app.config import Settings, get_settings
from app.models import InvokeRequest
from app.telemetry import with_span

router = APIRouter(prefix="/api", tags=["invoke"])

# Maps the LangChain tool name (what astream_events reports) to which scope
# it needs — used to turn a NEEDS_AGENT_AUTH_MARKER sentinel into a specific
# `auth_required` SSE event the frontend can act on without parsing prose.
_TOOL_REQUIRED_SCOPE_SETTING = {
    "ask_task_agent_read": "todos_read_scope",
    "ask_task_agent_write": "todos_write_scope",
}


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


@router.post("/invoke")
async def invoke(request: Request, body: InvokeRequest, settings: Settings = Depends(get_settings)):
    # Two privilege tiers, not one blanket gate:
    #  - Plain chat never touches a protected resource, so the signed-in
    #    session (already fully verified once, at OIDC login) is sufficient.
    #  - Acting on the user's behalf via A2A (ask_task_agent_*) is the actual
    #    AgentCore-style inbound-auth boundary, scoped per action — that
    #    still requires a fresh, independently-verified delegated token for
    #    exactly the scope in question, enforced below AND, more importantly,
    #    re-enforced independently by the Task Agent itself (which also
    #    checks the token's *scope*, not just its validity).
    session = read_cookie(request, SESSION_COOKIE, settings)
    if not session or not session.get("access_token"):
        raise HTTPException(status_code=401, detail="Sign in with PingOne before chatting.")

    sub = session.get("sub")
    agent_client_id: str | None = None
    verified_bearer_tokens: dict[str, str] = {}  # scope -> access_token

    exchanged_by_scope = read_cookie(request, EXCHANGED_TOKEN_COOKIE, settings) or {}
    for scope, entry in exchanged_by_scope.items():
        candidate_token = entry.get("access_token")
        if not candidate_token:
            continue
        with with_span("inbound_auth.verify", {"oauth.scope": scope}) as auth_span:
            try:
                identity = await verify_inbound_token(candidate_token, settings)
            except InboundAuthError as exc:
                # Present but no longer valid (expired, tampered, wrong
                # audience/scope) — degrade gracefully: this one scope just
                # isn't usable, rather than failing the whole request.
                # ask_task_agent_* will surface the need to re-approve if the
                # user asks for something that actually needs it.
                auth_span.set_attribute("inbound_auth.failure_reason", exc.reason)
                auth_span.set_status(Status(StatusCode.ERROR, exc.reason))
                continue
            auth_span.set_attribute("identity.sub", identity.sub or "")
            auth_span.set_attribute("identity.agent_client_id", identity.client_id or "")
            if identity.actor_sub:
                auth_span.set_attribute("identity.actor_sub", identity.actor_sub)
            sub = identity.sub or sub
            agent_client_id = identity.client_id
            verified_bearer_tokens[scope] = candidate_token

    graph = request.app.state.graph

    async def event_stream() -> AsyncIterator[dict]:
        with with_span(
            "agent.invoke",
            {
                "identity.token_source": "exchanged" if verified_bearer_tokens else "session",
                "identity.sub": sub or "",
                "identity.agent_client_id": agent_client_id or "",
                "identity.granted_scopes": " ".join(sorted(verified_bearer_tokens)),
                "agent.thread_id": body.thread_id,
            },
        ) as span:
            try:
                output_chars = 0
                async for event in graph.astream_events(
                    {"messages": [("human", body.message)]},
                    config={
                        "configurable": {
                            "thread_id": body.thread_id,
                            # Keyed by scope; empty for scopes not yet approved —
                            # ask_task_agent_* (app/agent/tools.py) treats a
                            # missing entry as "needs approval for this scope"
                            # rather than attempting the A2A call with nothing.
                            "bearer_tokens": verified_bearer_tokens,
                            "task_agent_url": settings.task_agent_url,
                            "todos_read_scope": settings.todos_read_scope,
                            "todos_write_scope": settings.todos_write_scope,
                            "caller_sub": sub or "",
                            "caller_agent_client_id": agent_client_id or "",
                        }
                    },
                    version="v2",
                ):
                    if event["event"] == "on_tool_end":
                        output = event["data"].get("output")
                        content = getattr(output, "content", "")
                        tool_name = event.get("name")
                        scope_setting = _TOOL_REQUIRED_SCOPE_SETTING.get(tool_name)
                        if (
                            isinstance(content, str)
                            and content.startswith(NEEDS_AGENT_AUTH_MARKER)
                            and scope_setting
                        ):
                            required_scope = getattr(settings, scope_setting)
                            yield {"event": "auth_required", "data": json.dumps({"scope": required_scope})}
                        continue
                    if event["event"] != "on_chat_model_stream":
                        continue
                    text = _extract_text(event["data"]["chunk"].content)
                    if not text:
                        continue
                    output_chars += len(text)
                    yield {"event": "token", "data": json.dumps({"text": text})}
                span.set_attribute("agent.output_chars", output_chars)
                yield {"event": "done", "data": json.dumps({})}
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}

    return EventSourceResponse(event_stream())
