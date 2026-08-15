from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from opentelemetry.trace import Status, StatusCode
from sse_starlette.sse import EventSourceResponse

from app.auth.session import AGENT_TOKEN_COOKIE, EXCHANGED_TOKEN_COOKIE, SESSION_COOKIE, read_cookie
from app.config import Settings, get_settings
from app.models import InvokeRequest
from app.telemetry import with_span

router = APIRouter(prefix="/api", tags=["invoke"])


def _resolve_bearer(request: Request, settings: Settings) -> tuple[str | None, str]:
    """Bearer priority: exchanged token > session token > none."""
    exchanged = read_cookie(request, EXCHANGED_TOKEN_COOKIE, settings)
    if exchanged and exchanged.get("access_token"):
        return exchanged["access_token"], "exchanged"
    session = read_cookie(request, SESSION_COOKIE, settings)
    if session and session.get("access_token"):
        return session["access_token"], "session"
    return None, "none"


def _identity_attributes(request: Request, settings: Settings, source: str) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    session = read_cookie(request, SESSION_COOKIE, settings)
    if session and session.get("sub"):
        attributes["identity.sub"] = session["sub"]
    if source == "exchanged":
        agent = read_cookie(request, AGENT_TOKEN_COOKIE, settings)
        if agent and agent.get("client_id"):
            attributes["identity.agent_client_id"] = agent["client_id"]
    return attributes


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
    token, source = _resolve_bearer(request, settings)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in and authenticate the agent before chatting.")

    graph = request.app.state.graph
    identity = _identity_attributes(request, settings, source)

    async def event_stream() -> AsyncIterator[dict]:
        with with_span(
            "agent.invoke",
            {"identity.token_source": source, "agent.thread_id": body.thread_id, **identity},
        ) as span:
            try:
                output_chars = 0
                async for event in graph.astream_events(
                    {"messages": [("human", body.message)]},
                    config={"configurable": {"thread_id": body.thread_id}},
                    version="v2",
                ):
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
