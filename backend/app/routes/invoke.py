from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from opentelemetry.trace import Status, StatusCode
from sse_starlette.sse import EventSourceResponse

from app.agent.graph import resolved_model_label
from app.agent.tools import NEEDS_AGENT_AUTH_MARKER
from app.auth.inbound import InboundAuthError, verify_inbound_token
from app.auth.session import EXCHANGED_TOKEN_COOKIE, SESSION_COOKIE, read_cookie
from app.config import Settings, get_settings
from app.models import InvokeRequest
from app.telemetry import with_span

router = APIRouter(prefix="/api", tags=["invoke"])

# Tool names that delegate to the Task Agent — used to turn a
# NEEDS_AGENT_AUTH_MARKER sentinel into a deterministic `auth_required` SSE
# event the frontend can act on without parsing prose. There's only one
# delegation credential now (not one per action), so this is just "was it
# a delegating tool call", not a lookup into a per-scope setting.
_DELEGATING_TOOLS = {"ask_task_agent_read", "ask_task_agent_write"}


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
    delegated_token: str | None = None

    delegated_entry = read_cookie(request, EXCHANGED_TOKEN_COOKIE, settings)
    candidate_token = (delegated_entry or {}).get("access_token")
    if candidate_token:
        with with_span("inbound_auth.verify") as auth_span:
            try:
                # This token is a delegation credential addressed to the
                # Task Agent (audience = its own URL), not to this service
                # — the Chat Agent no longer holds a todos-scoped token of
                # its own to verify against its own audience. See
                # CLAUDE.md (2026-08-16 redesign, in progress).
                identity = await verify_inbound_token(
                    candidate_token,
                    settings,
                    expected_audience=settings.task_agent_expected_audience or settings.task_agent_url,
                )
            except InboundAuthError as exc:
                # Present but no longer valid (expired, tampered, wrong
                # audience/scope) — degrade gracefully: just treat it as
                # not delegated yet, rather than failing the whole request.
                # ask_task_agent_* will surface the need to re-approve if
                # the user asks for something that actually needs it.
                auth_span.set_attribute("inbound_auth.failure_reason", exc.reason)
                auth_span.set_status(Status(StatusCode.ERROR, exc.reason))
            else:
                auth_span.set_attribute("identity.sub", identity.sub or "")
                # `agent_client_id` (PingOne's custom claim, propagated from
                # the actor token used in the exchange that produced this
                # token) is "which agent is delegating" — `client_id` is
                # just whichever app performed the exchange call, a
                # mechanical detail. Confirmed via a real PingOne token
                # 2026-08-16; same distinction task-agent's policy ACL uses.
                auth_span.set_attribute("identity.agent_client_id", identity.agent_client_id or "")
                auth_span.set_attribute("identity.exchange_client_id", identity.client_id or "")
                if identity.actor_sub:
                    auth_span.set_attribute("identity.actor_sub", identity.actor_sub)
                sub = identity.sub or sub
                agent_client_id = identity.agent_client_id
                delegated_token = candidate_token

    graph = request.app.state.graph

    async def event_stream() -> AsyncIterator[dict]:
        with with_span(
            "agent.invoke",
            {
                "agent.model_provider": settings.model_provider,
                "agent.model": resolved_model_label(settings),
                "identity.token_source": "exchanged" if delegated_token else "session",
                "identity.sub": sub or "",
                "identity.agent_client_id": agent_client_id or "",
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
                            # None if not yet approved — ask_task_agent_*
                            # (app/agent/tools.py) treats a missing token as
                            # "needs approval" rather than attempting the
                            # A2A call with nothing.
                            "delegated_token": delegated_token,
                            "task_agent_url": settings.task_agent_url,
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
                        if (
                            isinstance(content, str)
                            and content.startswith(NEEDS_AGENT_AUTH_MARKER)
                            and tool_name in _DELEGATING_TOOLS
                        ):
                            yield {"event": "auth_required", "data": json.dumps({})}
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
