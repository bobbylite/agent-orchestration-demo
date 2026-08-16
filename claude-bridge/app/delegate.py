"""Real A2A client call to the Task Agent — same protocol
backend/app/agent/tools.py uses (Agent Cards, task-based JSON-RPC over
HTTP, not an in-process call), just triggered by Claude Desktop's own
tool-calling instead of a LangGraph tool node. The Task Agent independently
re-verifies whatever token it's handed here — nothing about this bridge is
trusted on say-so, same as every other hop in this app.
"""

from __future__ import annotations

import logging

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest, TaskState

logger = logging.getLogger(__name__)

_STATE_LABELS = {
    TaskState.TASK_STATE_COMPLETED: "completed",
    TaskState.TASK_STATE_FAILED: "failed",
    TaskState.TASK_STATE_WORKING: "working",
    TaskState.TASK_STATE_REJECTED: "rejected",
}


def _task_agent_confirmation(task) -> str | None:
    """Pulls the Task Agent's own "Verified caller (sub=..., ...)" status
    message out of task history — its independent confirmation of who it
    thinks it's talking to, not just this bridge's own belief."""
    for message in task.history:
        if message.role == Role.ROLE_AGENT:
            for part in message.parts:
                if part.text.startswith("Verified caller"):
                    return part.text
    return None


async def ask_task_agent(request: str, *, delegation_token: str, task_agent_url: str) -> str:
    headers = {"Authorization": f"Bearer {delegation_token}"}
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
            logger.info("Task Agent state: %s", _STATE_LABELS.get(task.status.state, "unknown"))
            confirmation = _task_agent_confirmation(task)
            if confirmation:
                logger.info("Task Agent's own identity confirmation: %s", confirmation)
            if task.status.state == TaskState.TASK_STATE_FAILED and task.status.HasField("message"):
                parts = task.status.message.parts
                if parts:
                    logger.warning("Task Agent reported failure: %s", parts[-1].text)
            if task.artifacts:
                parts = task.artifacts[-1].parts
                if parts:
                    answer = parts[-1].text
        await client.close()

    return answer or "The Task Agent did not return a response."
