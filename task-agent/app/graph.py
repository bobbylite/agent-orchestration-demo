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
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_mcp_adapters.client import MultiServerMCPClient
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


async def _get_mcp_tools(settings: Settings) -> list:
    client = MultiServerMCPClient(
        {
            "todos": {
                "transport": "streamable_http",
                "url": settings.mcp_todos_url,
            }
        }
    )
    return await client.get_tools()


async def _policy_wrap_tool_call(request: ToolCallRequest, execute):
    tool_name = request.tool_call["name"]
    client_id = (request.runtime.config or {}).get("configurable", {}).get("client_id")
    if not policy.check(tool_name, client_id):
        return ToolMessage(
            content=f"Denied: agent '{client_id}' is not authorized to use tool '{tool_name}'.",
            tool_call_id=request.tool_call["id"],
        )
    return await execute(request)


def _build_task_assistant_node(settings: Settings, tools: list):
    llm = ChatAnthropic(
        model_name=settings.agent_model,
        api_key=SecretStr(settings.anthropic_api_key or ""),
        timeout=None,
        stop=None,
    ).bind_tools(tools)

    async def task_assistant(state: AgentState, config: RunnableConfig) -> AgentState:
        response = await llm.ainvoke(state["messages"], config=config)
        return {"messages": [response]}

    return task_assistant


async def build_graph(settings: Settings) -> CompiledStateGraph:
    tools = await _get_mcp_tools(settings)

    graph = StateGraph(AgentState)
    graph.add_node("task_assistant", _build_task_assistant_node(settings, tools))
    graph.add_node("execute_tool", ToolNode(tools, awrap_tool_call=_policy_wrap_tool_call))
    graph.set_entry_point("task_assistant")
    graph.add_conditional_edges("task_assistant", tools_condition, {"tools": "execute_tool", END: END})
    graph.add_edge("execute_tool", "task_assistant")
    return graph.compile()
