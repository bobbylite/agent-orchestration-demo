"""Wires inbound auth into the A2A task lifecycle.

Inbound auth runs first, inside execute(), before the graph is touched at
all — same rule as backend/app/routes/invoke.py: auth is never something
the graph's own control flow can be reached without passing. See
CLAUDE.md's "core architectural decision" section.
"""

from __future__ import annotations

from a2a.helpers import get_message_text, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from agentcore_shared import InboundAuthError, verify_bearer_token
from langgraph.graph.state import CompiledStateGraph

from app.config import Settings


class TaskAgentExecutor(AgentExecutor):
    def __init__(self, graph: CompiledStateGraph, settings: Settings) -> None:
        self.graph = graph
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
            message=new_text_message(f"Verified caller (sub={identity.sub}, client_id={identity.client_id})")
        )

        query = get_message_text(context.message)
        result = await self.graph.ainvoke(
            {"messages": [("human", query)]},
            config={"configurable": {"client_id": identity.client_id, "thread_id": task.context_id}},
        )
        answer = result["messages"][-1].content

        await task_updater.add_artifact(parts=[new_text_part(text=answer, media_type="text/plain")])
        await task_updater.complete(message=new_text_message("Done."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")
