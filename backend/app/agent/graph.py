"""The LangGraph agent invoked by /api/invoke.

Two nodes: `assistant` reasons (including deciding whether to delegate to
the Task Agent via the `ask_task_agent` tool), `tools` executes that
delegation over the real A2A protocol (app/agent/tools.py) — not an
in-process call. Standard LangGraph ReAct loop shape via the prebuilt
ToolNode/tools_condition, same as the Task Agent's own graph
(task-agent/app/graph.py).

MemorySaver checkpoints conversation state per thread_id, so the frontend
only ever needs to send the newest user message, not the full history.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import SecretStr

from app.agent.tools import ask_task_agent
from app.config import Settings

_TOOLS = [ask_task_agent]


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _build_assistant_node(settings: Settings):
    # timeout/stop passed explicitly (at their real defaults) because
    # langchain-anthropic declares them as `Field(None, alias=...)` —
    # Pylance's pydantic plugin doesn't recognize that positional-default
    # form and flags them as missing-required otherwise. Harmless either
    # way; this just satisfies the type checker.
    llm = ChatAnthropic(
        model_name=settings.agent_model,
        api_key=SecretStr(settings.anthropic_api_key or ""),
        timeout=None,
        stop=None,
    ).bind_tools(_TOOLS)

    async def assistant(state: AgentState, config: RunnableConfig) -> AgentState:
        response = await llm.ainvoke(state["messages"], config=config)
        return {"messages": [response]}

    return assistant


def build_graph(settings: Settings) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("assistant", _build_assistant_node(settings))
    graph.add_node("tools", ToolNode(_TOOLS))
    graph.set_entry_point("assistant")
    graph.add_conditional_edges("assistant", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "assistant")
    return graph.compile(checkpointer=MemorySaver())
