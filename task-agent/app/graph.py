"""The Task Agent's own graph — independent reasoning (task_assistant) plus
a policy-gated tool node (execute_tool) backed by the todos MCP server.

Same two-node assistant<->tools loop shape as the Chat Agent
(backend/app/agent/graph.py), for the same reason: this is the standard
LangGraph ReAct pattern, not something bespoke per agent.

Policy enforcement (`app/policy.py`) is wired in via ToolNode's
`awrap_tool_call` hook — checked per tool call, before the MCP server is
ever touched, using the verified caller identity threaded through
RunnableConfig by app/agent_executor.py. This keeps authorization out of
the graph's own reasoning path per CLAUDE.md's core rule: it's a plain
function call around the tool node, not something the model decides.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StreamableHttpConnection
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import SecretStr

from app import policy
from app.config import Settings


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


async def _get_mcp_tools(settings: Settings, bearer_token: str | None) -> list:
    connection: StreamableHttpConnection = {
        "transport": "streamable_http",
        "url": settings.mcp_todos_url,
    }
    if bearer_token:
        # Forwarded so mcp-todos-server can independently verify the same
        # delegated token and attribute the call to the real human it was
        # issued for (OBO audit log) — not something task-agent asserts on
        # its behalf. See CLAUDE.md "Identity propagation across the A2A hop".
        connection["headers"] = {"Authorization": f"Bearer {bearer_token}"}
    client = MultiServerMCPClient({"todos": connection})
    return await client.get_tools()


async def _policy_wrap_tool_call(request: ToolCallRequest, execute):
    tool_name = request.tool_call["name"]
    configurable = (request.runtime.config or {}).get("configurable", {})
    client_id = configurable.get("client_id")
    granted_scope = configurable.get("granted_scope")
    if not policy.check(tool_name, client_id, granted_scope):
        return ToolMessage(
            content=(
                f"Denied: agent '{client_id}' (granted scope: {granted_scope!r}) is not authorized "
                f"to use tool '{tool_name}'."
            ),
            tool_call_id=request.tool_call["id"],
        )
    return await execute(request)


_SYSTEM = SystemMessage(
    content=(
        "You are the Task Agent, a specialist backend agent that manages a todo list via MCP "
        "tools (list_todos, add_todo, complete_todo). Your final answer is consumed by another "
        "AI agent (the Chat Agent), not read directly by a human, so preserve structured details "
        "instead of writing a purely prose summary that drops them.\n\n"
        "Every todo has an `id`. Whenever you report todos — after list_todos, or after "
        "add_todo — always include each todo's id explicitly next to its text. The caller has no "
        "other way to obtain an id and needs it to complete a specific todo later.\n\n"
        "If you're asked to complete a todo by name or description rather than by id, don't "
        "refuse or ask for an id — call list_todos yourself first, match the todo by its text, "
        "then call complete_todo with the id you found, all within this same turn.\n\n"
        "If a tool call is denied or fails, explain the real reason plainly and concisely."
    )
)


def _build_task_assistant_node(settings: Settings, tools: list):
    llm = ChatAnthropic(
        model_name=settings.agent_model,
        api_key=SecretStr(settings.anthropic_api_key or ""),
        timeout=None,
        stop=None,
    ).bind_tools(tools)

    async def task_assistant(state: AgentState, config: RunnableConfig) -> AgentState:
        response = await llm.ainvoke([_SYSTEM, *state["messages"]], config=config)
        return {"messages": [response]}

    return task_assistant


async def build_graph(settings: Settings, bearer_token: str | None = None) -> CompiledStateGraph:
    tools = await _get_mcp_tools(settings, bearer_token)

    graph = StateGraph(AgentState)
    graph.add_node("task_assistant", _build_task_assistant_node(settings, tools))
    graph.add_node("execute_tool", ToolNode(tools, awrap_tool_call=_policy_wrap_tool_call))
    graph.set_entry_point("task_assistant")
    graph.add_conditional_edges("task_assistant", tools_condition, {"tools": "execute_tool", END: END})
    graph.add_edge("execute_tool", "task_assistant")
    return graph.compile()
