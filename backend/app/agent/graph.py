"""The LangGraph agent invoked by /api/invoke.

Deliberately a single node in v1 — the extension points for later are:
  - additional nodes that call sidecar HTTP/MCP services
  - a supervisor/router node for multi-agent handoff
  - exposing this compiled graph behind an A2A-compatible endpoint

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
from pydantic import SecretStr

from app.config import Settings


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
    )

    async def assistant(state: AgentState, config: RunnableConfig) -> AgentState:
        response = await llm.ainvoke(state["messages"], config=config)
        return {"messages": [response]}

    return assistant


def build_graph(settings: Settings) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("assistant", _build_assistant_node(settings))
    graph.set_entry_point("assistant")
    graph.add_edge("assistant", END)
    return graph.compile(checkpointer=MemorySaver())
