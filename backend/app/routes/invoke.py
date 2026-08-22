from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from opentelemetry.trace import Status, StatusCode
from sse_starlette.sse import EventSourceResponse

from app.agent.graph import resolved_model_label
from app.agent.tools import CIBA_REQUIRED_MARKER
from app.auth import ciba, ciba_store
from app.auth.inbound import InboundAuthError, verify_inbound_token
from app.auth.routes import _mint_agent_token
from app.auth.session import CIBA_TOKEN_COOKIE, EXCHANGED_TOKEN_COOKIE, SESSION_COOKIE, read_cookie, set_sealed_cookie
from app.config import Settings, get_settings
from app.models import InvokeRequest
from app.telemetry import with_span

router = APIRouter(prefix="/api", tags=["invoke"])
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
    session = read_cookie(request, SESSION_COOKIE, settings)
    if not session or not session.get("access_token"):
        raise HTTPException(status_code=401, detail="Sign in with PingOne before chatting.")

    sub = session.get("sub")
    agent_client_id: str | None = None
    delegated_token: str | None = None
    ciba_token: str | None = ciba_store.get_token(session.get("sub", ""))
    ciba_entry = read_cookie(request, CIBA_TOKEN_COOKIE, settings)
    if ciba_entry and isinstance(ciba_entry.get("access_token"), str):
        ciba_token = ciba_entry["access_token"]

    delegated_entry = read_cookie(request, EXCHANGED_TOKEN_COOKIE, settings)
    candidate_token = (delegated_entry or {}).get("access_token")
    if candidate_token:
        with with_span("inbound_auth.verify") as auth_span:
            try:
                identity = await verify_inbound_token(
                    candidate_token,
                    settings,
                    expected_audience=settings.task_agent_expected_audience or settings.task_agent_url,
                )
            except InboundAuthError as exc:
                auth_span.set_attribute("inbound_auth.failure_reason", exc.reason)
                auth_span.set_status(Status(StatusCode.ERROR, exc.reason))
            else:
                auth_span.set_attribute("identity.sub", identity.sub or "")
                auth_span.set_attribute("identity.agent_client_id", identity.agent_client_id or "")
                auth_span.set_attribute("identity.exchange_client_id", identity.client_id or "")
                if identity.actor_sub:
                    auth_span.set_attribute("identity.actor_sub", identity.actor_sub)
                sub = identity.sub or sub
                agent_client_id = identity.agent_client_id
                delegated_token = candidate_token

    event_response: EventSourceResponse

    async def _start_and_poll_ciba(binding_message: str) -> dict[str, Any]:
        started = await ciba.start(
            settings,
            login_hint=session.get("email") or session.get("sub") or "",
            binding_message=binding_message,
        )
        deadline = time.monotonic() + min(started["expires_in"], settings.ciba_poll_timeout)
        interval = started["interval"]
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            status, token_body, slow_down = await ciba.poll(settings, started["auth_req_id"])
            if status == "approved" and token_body:
                return token_body
            if status == "terminal":
                raise ciba.CibaError("The CIBA approval was not completed")
            if slow_down:
                interval = min(interval + slow_down, settings.ciba_max_poll_interval)
        raise ciba.CibaError("Timed out waiting for PingOne approval")

    async def event_stream() -> AsyncIterator[dict]:
        nonlocal delegated_token, ciba_token, agent_client_id, sub
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
                # Generic delegation is automatic and deliberately happens
                # before CIBA. CIBA is a Task Policy step-up only.
                if not delegated_token:
                    try:
                        # The SSE response headers are already committed when
                        # this generator runs, so keep this request's token in
                        # memory. A later request can mint a fresh delegation
                        # token again if needed.
                        delegated_token = await _mint_agent_token(request, None, settings, session)
                        identity = await verify_inbound_token(
                            delegated_token,
                            settings,
                            expected_audience=settings.task_agent_expected_audience or settings.task_agent_url,
                        )
                        agent_client_id = identity.agent_client_id
                        sub = identity.sub or sub
                    except Exception as exc:  # fail closed before A2A
                        yield {"event": "error", "data": json.dumps({"message": f"Agent delegation failed: {exc}"})}
                        return

                ciba_attempted = False
                for attempt in range(2):
                    ciba_required = False
                    output_chars = 0
                    async for event in request.app.state.graph.astream_events(
                        {"messages": [("human", body.message)]},
                        config={
                            "configurable": {
                                "thread_id": body.thread_id,
                                "delegated_token": delegated_token,
                                "task_agent_url": settings.task_agent_url,
                                "caller_sub": sub or "",
                                "caller_agent_client_id": agent_client_id or "",
                                "ciba_token": ciba_token or "",
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
                                and content.startswith(CIBA_REQUIRED_MARKER)
                                and tool_name in _DELEGATING_TOOLS
                            ):
                                ciba_required = True
                            continue
                        if event["event"] != "on_chat_model_stream":
                            continue
                        text = _extract_text(event["data"]["chunk"].content)
                        if not text:
                            continue
                        output_chars += len(text)
                        yield {"event": "token", "data": json.dumps({"text": text})}

                    if not ciba_required or ciba_attempted:
                        span.set_attribute("agent.output_chars", output_chars)
                        if ciba_attempted and ciba_required:
                            yield {"event": "error", "data": json.dumps({"message": "Authorization could not be confirmed. Please follow the instructions in the email and try again."})}
                        else:
                            yield {"event": "done", "data": json.dumps({})}
                        return

                    ciba_attempted = True
                    binding_message = ciba.generate_binding_message(settings.ciba_binding_message_length)
                    yield {
                        "event": "authorization_required",
                        "data": json.dumps({"email": session.get("email") or session.get("preferred_username") or session.get("sub") or "your account", "binding_message": binding_message}),
                    }
                    try:
                        token_body = await _start_and_poll_ciba(binding_message)
                    except ciba.CibaError as exc:
                        yield {"event": "error", "data": json.dumps({"message": str(exc)})}
                        return
                    ciba_token = token_body["access_token"]
                    ciba_store.store_token(session.get("sub", ""), ciba_token, int(token_body.get("expires_in", settings.ciba_token_max_age)))
                    # The SSE response has already started, so Set-Cookie
                    # cannot be added here. The token stays in this request's
                    # closure and is sent on the immediate retry below.

                yield {"event": "error", "data": json.dumps({"message": "The authorization flow could not be completed."})}
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}

    event_response = EventSourceResponse(event_stream())
    return event_response
