from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from opentelemetry.trace import Status, StatusCode
from sse_starlette.sse import EventSourceResponse

from app.auth.inbound import InboundAuthError, InboundIdentity, verify_inbound_token
from app.auth.session import EXCHANGED_TOKEN_COOKIE, read_cookie
from app.config import Settings, get_settings
from app.models import InvokeRequest
from app.telemetry import with_span

router = APIRouter(prefix="/api", tags=["invoke"])


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
    # Inbound auth, AgentCore-style: only a delegated token from RFC 8693
    # token exchange is accepted — a plain signed-in session is not enough,
    # because its audience was never scoped to this agent. The token is
    # re-verified fresh below rather than trusted just because it's sealed
    # in one of our own cookies.
    exchanged = read_cookie(request, EXCHANGED_TOKEN_COOKIE, settings)
    token = exchanged.get("access_token") if exchanged else None
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authenticate the agent (Client Credentials + Token Exchange) before chatting — "
            "being signed in alone is not sufficient.",
        )

    with with_span("inbound_auth.verify") as auth_span:
        try:
            identity: InboundIdentity = await verify_inbound_token(token, settings)
        except InboundAuthError as exc:
            auth_span.set_attribute("inbound_auth.failure_reason", exc.reason)
            auth_span.set_status(Status(StatusCode.ERROR, exc.reason))
            raise HTTPException(status_code=401, detail=f"Inbound auth rejected the token: {exc.reason}") from exc
        auth_span.set_attribute("identity.sub", identity.sub or "")
        auth_span.set_attribute("identity.agent_client_id", identity.client_id or "")
        if identity.actor_sub:
            auth_span.set_attribute("identity.actor_sub", identity.actor_sub)

    graph = request.app.state.graph

    async def event_stream() -> AsyncIterator[dict]:
        with with_span(
            "agent.invoke",
            {
                "identity.token_source": "exchanged",
                "identity.sub": identity.sub or "",
                "identity.agent_client_id": identity.client_id or "",
                "agent.thread_id": body.thread_id,
            },
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
