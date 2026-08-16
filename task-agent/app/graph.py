"""The Task Agent's own graph — independent reasoning (task_assistant) plus
a scope-stepping tool node (execute_tool) backed by the todos MCP server.

Same two-node assistant<->tools loop shape as the Chat Agent
(backend/app/agent/graph.py), for the same reason: this is the standard
LangGraph ReAct pattern, not something bespoke per agent.

The tools bound to the LLM (and to ToolNode, for routing) are fetched once
per task purely for their schemas — mcp-todos-server's tools/list doesn't
require auth, only actual tool *calls* do (see mcp-todos-server/app/mcp_server.py).
The real work happens in `_scoped_tool_call` (wired in via ToolNode's
`awrap_tool_call` hook, same mechanism policy enforcement already used):
per call, it performs this service's own Client Credentials (proving "the
Task Agent" itself) and then a fresh RFC 8693 Token Exchange — subject =
the delegation token received from the Chat Agent (so the human's identity
survives the hop, not just this service's own), scoped to exactly the
todos capability THIS tool needs — then builds a freshly-authorized MCP
connection and invokes the real tool through it. This is what makes
"step-up" scoping work for free: nothing is cached across different
scopes, so a read succeeding earlier never gates whether a later write is
attempted, and vice versa. See CLAUDE.md "Identity propagation across the
A2A hop" (2026-08-16 redesign).
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

import httpx
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
from app.token_grants import client_credentials_grant, get_token_endpoint, token_exchange


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _pingone_error_detail(exc: httpx.HTTPStatusError) -> str:
    try:
        body = exc.response.json()
        return str(body.get("error_description") or body.get("error") or exc.response.text)
    except ValueError:
        return exc.response.text or str(exc)


async def _get_mcp_tools(settings: Settings) -> list:
    """Schema only — no Authorization header. tools/list doesn't require
    one; actual invocation (_scoped_tool_call, below) always builds its own
    freshly-authorized connection instead of reusing these tool objects."""
    connection: StreamableHttpConnection = {"transport": "streamable_http", "url": settings.mcp_todos_url}
    client = MultiServerMCPClient({"todos": connection})
    return await client.get_tools()


async def _get_mcp_tool(settings: Settings, tool_name: str, bearer_token: str):
    connection: StreamableHttpConnection = {
        "transport": "streamable_http",
        "url": settings.mcp_todos_url,
        "headers": {"Authorization": f"Bearer {bearer_token}"},
    }
    client = MultiServerMCPClient({"todos": connection})
    for tool in await client.get_tools():
        if tool.name == tool_name:
            return tool
    raise ValueError(f"mcp-todos-server does not expose a tool named {tool_name!r}")


# Tool name -> which Settings attribute names the scope to request for it.
_REQUIRED_SCOPE_SETTING = {
    "list_todos": "todos_read_scope",
    "add_todo": "todos_write_scope",
    "complete_todo": "todos_write_scope",
}


async def _get_mcp_scoped_token(settings: Settings, actor_cache: dict[str, Any], *, delegation_token: str, scope: str) -> str:
    """Steps 2 and 3 of this service's own delegation chain — step 2 (this
    service's own Client Credentials) is cached across calls within one
    task (it doesn't vary by tool); step 3 (the actual MCP-scoped Token
    Exchange) always runs fresh, which is what makes step-up scoping work.
    """
    if "token_endpoint" not in actor_cache:
        actor_cache["token_endpoint"] = await get_token_endpoint(settings.oidc_discovery_url or "")
    token_endpoint = actor_cache["token_endpoint"]

    if "actor_token" not in actor_cache:
        actor = await client_credentials_grant(
            token_endpoint,
            client_id=settings.task_agent_client_id,
            client_secret=settings.task_agent_client_secret,
            scope=settings.agent_task_scope,
        )
        actor_cache["actor_token"] = actor["access_token"]

    exchanged = await token_exchange(
        token_endpoint,
        client_id=settings.todos_mcp_client_id,
        client_secret=settings.todos_mcp_client_secret,
        subject_token=delegation_token,
        actor_token=actor_cache["actor_token"],
        scope=scope,
    )
    return exchanged["access_token"]


async def _scoped_tool_call(settings: Settings, actor_cache: dict[str, Any], request: ToolCallRequest) -> ToolMessage:
    tool_name = request.tool_call["name"]
    configurable = (request.runtime.config or {}).get("configurable", {})
    client_id = configurable.get("client_id")
    delegation_token = configurable.get("delegation_token")

    if not policy.check(tool_name, client_id):
        return ToolMessage(
            content=f"Denied: agent '{client_id}' is not authorized to use tool '{tool_name}'.",
            tool_call_id=request.tool_call["id"],
        )

    scope_setting = _REQUIRED_SCOPE_SETTING.get(tool_name)
    if not scope_setting or not delegation_token:
        return ToolMessage(
            content=f"Cannot call '{tool_name}': no delegation token available for this task.",
            tool_call_id=request.tool_call["id"],
        )
    required_scope = getattr(settings, scope_setting)

    try:
        mcp_token = await _get_mcp_scoped_token(
            settings, actor_cache, delegation_token=delegation_token, scope=required_scope
        )
    except httpx.HTTPStatusError as exc:
        return ToolMessage(
            content=(
                f"Could not obtain {required_scope!r} access to the todos service for this request — "
                f"PingOne rejected the token exchange: {_pingone_error_detail(exc)}. The user may need "
                f"to grant this specific permission."
            ),
            tool_call_id=request.tool_call["id"],
        )

    try:
        tool = await _get_mcp_tool(settings, tool_name, mcp_token)
        result = await tool.ainvoke(request.tool_call["args"])
    except Exception as exc:  # noqa: BLE001 — surfaced to the model as a normal tool result, not a crash
        return ToolMessage(content=f"The todos service call failed: {exc}", tool_call_id=request.tool_call["id"])

    return ToolMessage(content=str(result), tool_call_id=request.tool_call["id"])


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


async def build_graph(settings: Settings) -> CompiledStateGraph:
    tools = await _get_mcp_tools(settings)
    actor_cache: dict[str, Any] = {}

    async def _wrap(request: ToolCallRequest, execute):
        return await _scoped_tool_call(settings, actor_cache, request)

    graph = StateGraph(AgentState)
    graph.add_node("task_assistant", _build_task_assistant_node(settings, tools))
    graph.add_node("execute_tool", ToolNode(tools, awrap_tool_call=_wrap))
    graph.set_entry_point("task_assistant")
    graph.add_conditional_edges("task_assistant", tools_condition, {"tools": "execute_tool", END: END})
    graph.add_edge("execute_tool", "task_assistant")
    return graph.compile()
