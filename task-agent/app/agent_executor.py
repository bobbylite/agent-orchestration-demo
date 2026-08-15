"""Wires inbound auth into the A2A task lifecycle.

Inbound auth runs first, inside execute(), before the graph is touched at
all — same rule as backend/app/routes/invoke.py: auth is never something
the graph's own control flow can be reached without passing. See
CLAUDE.md's "core architectural decision" section.
"""

from __future__ import annotations

from typing import Any

from a2a.helpers import get_message_text, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from agentorchestration_shared import InboundAuthError, verify_bearer_token

from app.config import Settings
from app.graph import build_graph


def _extract_text(content: Any) -> str:
    """AIMessage.content is `str | list[dict]` depending on the response
    shape (e.g. a multi-block Anthropic response) — new_text_part() needs a
    plain str, so this normalizes either shape. Same pattern as
    backend/app/routes/invoke.py's own _extract_text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


class TaskAgentExecutor(AgentExecutor):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task:
            task = context.current_task
        else:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        task_updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)

        headers = context.call_context.state.get("headers", {})
        auth_header = headers.get("authorization", "")
        token = auth_header.removeprefix("Bearer ").strip()

        if not token:
            await task_updater.failed(message=new_text_message("Missing bearer token."))
            return

        try:
            identity = await verify_bearer_token(
                token,
                discovery_url=self.settings.oidc_discovery_url or "",
                expected_audience=self.settings.agent_expected_audience or "",
            )
        except InboundAuthError as exc:
            await task_updater.failed(
                message=new_text_message(f"Inbound auth rejected the token: {exc.reason}")
            )
            return

        await task_updater.start_work(
            message=new_text_message(
                f"Verified caller (sub={identity.sub}, client_id={identity.client_id}, "
                f"scope={identity.scope})"
            )
        )

        # Rebuilt per call (not cached at startup) so the MCP connection to
        # mcp-todos-server carries *this* request's freshly-verified bearer
        # token — the same "verify/act fresh every time" rule as inbound
        # auth itself, not a fixed service-level credential. See
        # CLAUDE.md "Identity propagation across the A2A hop".
        graph = await build_graph(self.settings, bearer_token=token)
        query = get_message_text(context.message)
        result = await graph.ainvoke(
            {"messages": [("human", query)]},
            config={
                "configurable": {
                    "client_id": identity.client_id,
                    "granted_scope": identity.scope,
                    "thread_id": task.context_id,
                }
            },
        )
        answer = _extract_text(result["messages"][-1].content)

        await task_updater.add_artifact(parts=[new_text_part(text=answer, media_type="text/plain")])
        await task_updater.complete(message=new_text_message("Done."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")
