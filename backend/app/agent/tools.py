"""ask_task_agent_{read,write} — delegate to the Task Agent over the real A2A
protocol (not an in-process call). Two separate tools, not one with a
read/write parameter, so the model's own tool choice still shapes how it
phrases its request to the Task Agent — but that choice no longer selects
a different OAuth scope at this layer. Both tools forward the SAME
delegation token (Client Credentials + RFC 8693 Token Exchange, scoped
generically `agent:delegation`, addressed to the Task Agent) — this
service doesn't decide todos:read vs todos:write; the Task Agent does,
via its own further exchange, once it actually knows what's being asked.
See CLAUDE.md "Identity propagation across the A2A hop" (2026-08-16
redesign). The Task Agent independently re-verifies the token; nothing
here is trusted on say-so.
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
# a deterministic `auth_required` SSE event carrying which scope is needed —
# the frontend renders an inline "Approve Agent Action" prompt off of that
# event, not by parsing whatever prose the model wraps this content in. The
# model still sees the full sentence and explains it to the user in its own
# words; the marker is just for the frontend's benefit.
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


async def _delegate(request: str, config: RunnableConfig, *, intent: str) -> str:
    configurable = config.get("configurable", {})
    delegated_token: str | None = configurable.get("delegated_token")
    task_agent_url = configurable.get("task_agent_url")
    caller_sub = configurable.get("caller_sub", "")
    caller_agent_client_id = configurable.get("caller_agent_client_id", "")

    with with_span(
        "agent.a2a_delegate",
        {
            "a2a.task_agent_url": task_agent_url or "",
            "identity.sub": caller_sub,
            "identity.agent_client_id": caller_agent_client_id,
            "agent.intent": intent,
        },
    ) as span:
        if not task_agent_url:
            span.set_attribute("a2a.result", "misconfigured")
            return "The Task Agent is not reachable right now (missing configuration)."

        if not delegated_token:
            # Expected, common state — the user hasn't approved delegating
            # to the Task Agent yet. Not an error: they just need to
            # approve it (Client Credentials + RFC 8693 Token Exchange)
            # and then repeat their request.
            span.set_attribute("a2a.result", "needs_agent_auth")
            return (
                f"{NEEDS_AGENT_AUTH_MARKER}: To do this, the agent needs the user to approve delegating "
                "this task to the Task Agent first — it doesn't have a delegated token yet. Tell the "
                "user to click \"Approve Agent Action\" and then repeat their request."
            )

        headers = {"Authorization": f"Bearer {delegated_token}"}
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


@tool
async def ask_task_agent_read(request: str, config: RunnableConfig) -> str:
    """Delegate a READ-ONLY request to the Task Agent — use this for viewing
    or listing the user's todos. Never use this for adding, completing, or
    otherwise changing anything; use ask_task_agent_write for that."""
    return await _delegate(request, config, intent="read")


@tool
async def ask_task_agent_write(request: str, config: RunnableConfig) -> str:
    """Delegate a WRITE request to the Task Agent — use this for adding a
    new todo, completing one, reopening/undoing completion, or any other
    change to the user's todo list. Describe what to do in plain language
    (e.g. "mark 'buy milk' as complete" or "reopen 'buy milk'") — you do
    not need a todo's internal id first; the Task Agent looks it up itself."""
    return await _delegate(request, config, intent="write")
