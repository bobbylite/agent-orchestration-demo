"""ask_task_agent — delegates to the Task Agent over the real A2A protocol
(not an in-process call). Forwards the SAME RFC 8693 delegated token this
service already verified for its own /api/invoke inbound auth — see
CLAUDE.md "Identity propagation across the A2A hop". The Task Agent
independently re-verifies that token itself; nothing here is trusted on
the Task Agent's word alone — which is exactly what the span attributes
below are meant to make visible: this service's own view of who's calling,
alongside the Task Agent's independently-confirmed view of the same thing.
"""

from __future__ import annotations

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest, TaskState
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.telemetry import with_span

# Recognized by routes/invoke.py (checked against on_tool_end output) to emit
# a deterministic `auth_required` SSE event — the frontend renders an inline
# "Authenticate Agent" prompt off of that event, not by parsing whatever
# prose the model wraps this content in. The model still sees the full
# sentence and explains it to the user in its own words; the marker is just
# for the frontend's benefit, model behavior doesn't depend on it.
NEEDS_AGENT_AUTH_MARKER = "NEEDS_AGENT_AUTH"

_STATE_LABELS = {
    TaskState.TASK_STATE_COMPLETED: "completed",
    TaskState.TASK_STATE_FAILED: "failed",
    TaskState.TASK_STATE_WORKING: "working",
    TaskState.TASK_STATE_REJECTED: "rejected",
}


def _task_agent_confirmation(task) -> str | None:
    """Pulls the Task Agent's own "Verified caller (sub=..., client_id=...)"
    status message out of task history — its independent confirmation of
    who it thinks it's talking to, not just this service's own belief."""
    for message in task.history:
        if message.role == Role.ROLE_AGENT:
            for part in message.parts:
                if part.text.startswith("Verified caller"):
                    return part.text
    return None


@tool
async def ask_task_agent(request: str, config: RunnableConfig) -> str:
    """Delegate to the Task Agent for anything about the user's todo list —
    listing items, adding a new one, or marking one complete."""
    configurable = config.get("configurable", {})
    bearer_token = configurable.get("bearer_token")
    task_agent_url = configurable.get("task_agent_url")
    caller_sub = configurable.get("caller_sub", "")
    caller_agent_client_id = configurable.get("caller_agent_client_id", "")

    with with_span(
        "agent.a2a_delegate",
        {
            "a2a.task_agent_url": task_agent_url or "",
            "identity.sub": caller_sub,
            "identity.agent_client_id": caller_agent_client_id,
        },
    ) as span:
        if not task_agent_url:
            span.set_attribute("a2a.result", "misconfigured")
            return "The Task Agent is not reachable right now (missing configuration)."

        if not bearer_token:
            # Expected, common state — signing in alone doesn't authorize the
            # agent to act on the user's behalf. Not an error: the user just
            # needs to complete Client Credentials + Token Exchange first.
            span.set_attribute("a2a.result", "needs_agent_auth")
            return (
                f"{NEEDS_AGENT_AUTH_MARKER}: To do anything with the user's todo list, the agent "
                "must first authenticate itself and obtain a delegated token (RFC 8693 Token "
                "Exchange) — being signed in alone doesn't authorize acting on the user's behalf. "
                "Tell the user to click \"Authenticate Agent\" and then repeat their request."
            )

        headers = {"Authorization": f"Bearer {bearer_token}"}
        async with httpx.AsyncClient(headers=headers, timeout=30) as httpx_client:
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=task_agent_url)
            card = await resolver.get_agent_card()
            client = await create_client(
                agent=card, client_config=ClientConfig(streaming=False, httpx_client=httpx_client)
            )

            message = new_text_message(request, role=Role.ROLE_USER)
            send_request = SendMessageRequest(message=message)

            answer: str | None = None
            async for chunk in client.send_message(send_request):
                if not chunk.HasField("task"):
                    continue
                task = chunk.task
                span.set_attribute("a2a.task_state", _STATE_LABELS.get(task.status.state, "unknown"))
                confirmation = _task_agent_confirmation(task)
                if confirmation:
                    # The Task Agent's own report of who it verified — a second,
                    # independent read on identity, not derived from this
                    # service's own. See CLAUDE.md "Identity propagation".
                    span.set_attribute("a2a.task_agent_identity_confirmation", confirmation)
                if task.status.state == TaskState.TASK_STATE_FAILED and task.status.HasField("message"):
                    parts = task.status.message.parts
                    if parts:
                        span.set_attribute("a2a.failure_reason", parts[-1].text)
                if task.artifacts:
                    parts = task.artifacts[-1].parts
                    if parts:
                        answer = parts[-1].text
            await client.close()

        span.set_attribute("a2a.result", "ok" if answer else "empty")
        return answer or "The Task Agent did not return a response."
