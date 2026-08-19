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
from agentorchestration_shared import InboundAuthError, verify_bearer_token
from opentelemetry.trace import Status, StatusCode

from app.config import Settings
from app.graph import _extract_text, build_graph, resolved_agent_model_label
from app import policy
from app.telemetry import with_span


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

        # Its own top-level span, same as backend/app/routes/invoke.py's
        # inbound_auth.verify — a distinct phase (the gate) that happens
        # before the graph runs, not nested under the task-execution span.
        with with_span("inbound_auth.verify") as auth_span:
            try:
                identity = await verify_bearer_token(
                    token,
                    discovery_url=self.settings.oidc_discovery_url or "",
                    expected_audience=self.settings.agent_expected_audience or "",
                )
            except InboundAuthError as exc:
                auth_span.set_attribute("inbound_auth.failure_reason", exc.reason)
                auth_span.set_status(Status(StatusCode.ERROR, exc.reason))
                await task_updater.failed(
                    message=new_text_message(f"Inbound auth rejected the token: {exc.reason}")
                )
                return

            if not identity.has_scope(self.settings.expected_delegation_scope):
                auth_span.set_attribute("inbound_auth.failure_reason", "missing_scope")
                auth_span.set_status(Status(StatusCode.ERROR, "missing_scope"))
                await task_updater.failed(
                    message=new_text_message(
                        f"Inbound auth rejected the token: missing required scope "
                        f"{self.settings.expected_delegation_scope!r} (got {identity.scope!r})"
                    )
                )
                return

            auth_span.set_attribute("identity.sub", identity.sub or "")
            # `agent_client_id` (not `client_id`) — see CLAUDE.md "RFC 8693
            # chained delegation" for why the two differ and which one
            # policy.py's ACL actually checks.
            auth_span.set_attribute("identity.agent_client_id", identity.agent_client_id or "")
            auth_span.set_attribute("identity.exchange_client_id", identity.client_id or "")
            auth_span.set_attribute("oauth.scope", identity.scope or "")

        await task_updater.start_work(
            message=new_text_message(
                f"Verified caller (sub={identity.sub}, agent_client_id={identity.agent_client_id}, "
                f"client_id={identity.client_id}, scope={identity.scope})"
            )
        )

        # Top-level span for this task's execution — sibling to
        # inbound_auth.verify above (the gate), not nested under it, same
        # relationship backend/app/routes/invoke.py's agent.invoke has to
        # its own inbound_auth.verify. Every span the judge node emits
        # (judge.evaluate, once per attempt) nests under this one via
        # OTel's ambient context, so the trace shows the full
        # propose -> evaluate -> retry loop as one tree.
        with with_span(
            "a2a.task_execute",
            {
                "agent.model_provider": self.settings.model_provider,
                "agent.model": resolved_agent_model_label(self.settings),
                "identity.sub": identity.sub or "",
                "identity.agent_client_id": identity.agent_client_id or "",
                "a2a.task_id": task.id,
                "a2a.context_id": task.context_id,
            },
        ) as task_span:
            # Rebuilt per call (not cached at startup) — see CLAUDE.md
            # "Identity propagation across the A2A hop". The graph no longer
            # forwards `token` to mcp-todos-server as-is: it's threaded through
            # as `delegation_token`, used as the *subject* token for this
            # service's own RFC 8693 Token Exchange, performed fresh per tool
            # call once the graph knows which MCP capability it actually needs
            # (see app/graph.py's _scoped_tool_call).
            actor_cache: dict[str, object] = {}
            judge_budget, judge_budget_reason = await policy.evaluate_judge_budget(
                self.settings, subject_token=token,
                client_id=identity.agent_client_id,
            )
            graph = await build_graph(self.settings, actor_cache=actor_cache, judge_budget=judge_budget)
            task_span.set_attribute("judge.max_attempts", judge_budget or self.settings.judge_max_attempts)
            if judge_budget_reason:
                task_span.set_attribute("judge.budget_reason", judge_budget_reason)
            query = get_message_text(context.message)
            result = await graph.ainvoke(
                {
                    "messages": [("human", query)],
                    # NOT the human's literal message — this is the request the
                    # Chat Agent's own LLM constructed when it decided to
                    # delegate (query IS that string; see app/graph.py's
                    # AgentState docstring). What the judge node compares
                    # answers against.
                    "delegated_request": query,
                    "judge_attempts": 0,
                    "judge_status": "",
                },
                config={
                    "configurable": {
                        # `agent_client_id` (not `client_id`) — the custom claim
                        # PingOne propagates from the *actor* token used in the
                        # exchange that produced this token, i.e. which agent is
                        # actually delegating. `client_id` here is just whichever
                        # app performed the exchange call — not what
                        # policy.py's ACL should check. Confirmed via a real
                        # PingOne token 2026-08-16.
                        "client_id": identity.agent_client_id,
                        "granted_scope": identity.scope,
                        "delegation_token": token,
                        "thread_id": task.context_id,
                    }
                },
            )
            answer = _extract_text(result["messages"][-1].content)
            task_span.set_attribute("judge.final_status", result.get("judge_status") or "disabled")
            task_span.set_attribute("judge.attempts", result.get("judge_attempts", 0))

            await task_updater.add_artifact(parts=[new_text_part(text=answer, media_type="text/plain")])
            await task_updater.complete(message=new_text_message("Done."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")
