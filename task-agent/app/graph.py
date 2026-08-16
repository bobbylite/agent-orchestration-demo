"""The Task Agent's own graph — independent reasoning (task_assistant) plus
a scope-stepping tool node (execute_tool) backed by the todos MCP server,
plus a judge node that checks the proposed answer before it's returned.

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

The judge node (see "Judge agent" below) is a *quality* check, not a
security one — unlike everything above, it needs no identity of its own
and correctly lives inside the graph (CLAUDE.md's "auth never inside the
graph" rule is about authorization, not an LLM's own judgment of its work).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, NotRequired, TypedDict

import httpx
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableConfig
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StreamableHttpConnection
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import BaseModel, Field, SecretStr

from app import policy
from app.config import Settings
from app.telemetry import with_span
from app.token_grants import client_credentials_grant, get_token_endpoint, token_exchange

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    # Always present once the graph is running — the entry point always
    # seeds it. Individual nodes' *return values* are partial updates
    # though (a node returns only the keys it changed), which is why node
    # functions below are typed `-> dict`, not `-> AgentState` — annotating
    # them as the full state would incorrectly claim every node returns
    # every key.
    messages: Annotated[list[BaseMessage], add_messages]
    # The request this service was actually delegated — NOT the human's
    # literal message, which this service never sees (see the judge
    # section below for why that's the deliberately correct scope). Set
    # once at invocation.
    delegated_request: NotRequired[str]
    judge_attempts: NotRequired[int]
    judge_status: NotRequired[str]  # "" / "pass" / "fail" / "gave_up"


def _extract_text(content: Any) -> str:
    """AIMessage.content is `str | list[dict]` depending on the response
    shape (e.g. a multi-block Anthropic response) — normalize either shape
    before passing it anywhere that needs a real `str`. Canonical copy for
    this service; app/agent_executor.py imports it from here rather than
    keeping its own (same normalization backend/app/routes/invoke.py has
    its own separate copy of — different service, not shared)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


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


_DEFAULT_OPENAI_MODEL = "gpt-4.1"
_DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def _build_agent_llm(settings: Settings) -> BaseChatModel:
    """Same MODEL_PROVIDER branch as backend/app/agent/graph.py's
    _build_assistant_llm — kept as a separate copy per this service's own
    convention (outbound-facing code isn't shared via shared/)."""
    if settings.model_provider == "openai":
        return ChatOpenAI(
            model=settings.model_id or _DEFAULT_OPENAI_MODEL,
            api_key=SecretStr(settings.openai_api_key or ""),
        )
    if settings.model_provider == "groq":
        return ChatGroq(
            model=settings.groq_model or _DEFAULT_GROQ_MODEL,
            api_key=SecretStr(settings.groq_api_key or ""),
        )
    return ChatAnthropic(
        model_name=settings.agent_model,
        api_key=SecretStr(settings.anthropic_api_key or ""),
        timeout=None,
        stop=None,
    )


def resolved_agent_model_label(settings: Settings) -> str:
    """The actual model name task_assistant will run, given
    settings.model_provider — same resolution _build_agent_llm uses,
    exposed separately so agent_executor.py can stamp it onto the
    a2a.task_execute span without constructing a whole LLM client just to
    read a string. Mirrors backend/app/agent/graph.py's
    resolved_model_label."""
    if settings.model_provider == "openai":
        return settings.model_id or _DEFAULT_OPENAI_MODEL
    if settings.model_provider == "groq":
        return settings.groq_model or _DEFAULT_GROQ_MODEL
    return settings.agent_model


def _build_task_assistant_node(settings: Settings, tools: list):
    llm = _build_agent_llm(settings).bind_tools(tools)

    async def task_assistant(state: AgentState, config: RunnableConfig) -> dict:
        response = await llm.ainvoke([_SYSTEM, *state["messages"]], config=config)
        return {"messages": [response]}

    return task_assistant


# --- Judge agent -------------------------------------------------------
#
# A quality check, not a security one: evaluates task_assistant's proposed
# answer against `delegated_request` (the task this service was actually
# asked to do — see AgentState's docstring for why that's the deliberately
# correct comparison, not the human's original message) and decides
# whether it's good enough. Needs no identity of its own — it never
# touches a protected resource, only text already produced — so it
# correctly lives inside the graph, unlike every auth/policy check above.


class JudgeVerdict(BaseModel):
    status: Literal["pass", "fail"]
    reason: str
    missing_info: list[str] = Field(default_factory=list)


_JUDGE_SYSTEM = SystemMessage(
    content=(
        "You are a judge evaluating whether a specialist agent's proposed answer actually "
        "satisfies the request it was given. You will be shown the REQUEST and the PROPOSED "
        "ANSWER.\n\n"
        "Return status=\"pass\" if the answer genuinely addresses the request — and, when the "
        "request implied an action (adding or completing a todo), the answer shows real evidence "
        "the action was taken (e.g. a todo id), not just a claim that it was done.\n\n"
        "Return status=\"fail\" if the answer is incomplete, evasive, doesn't address the request, "
        "or claims an action happened with no evidence of it. Keep `reason` short and concrete; "
        "use `missing_info` to list specific things the answer should have included but didn't."
    )
)


_DEFAULT_GROQ_JUDGE_MODEL = "llama-3.3-70b-versatile"
_DEFAULT_OPENAI_JUDGE_MODEL = "gpt-4o-mini"


def _build_judge_llm(settings: Settings) -> BaseChatModel:
    """The judge only ever reads REQUEST/PROPOSED ANSWER text and returns a
    structured verdict — no tool calls of its own, no delegation token, no
    identity — so unlike task_assistant's LLM it isn't tied to Anthropic.
    `judge_provider="groq"` runs it on Groq's free tier instead (rate-
    limited, not billed); `judge_provider="openai"` reuses OPENAI_API_KEY
    (defaulting to a cheap model rather than MODEL_ID, since the judge
    doesn't need the same model strength as task_assistant). The model
    still needs tool-calling support since `.with_structured_output()`
    uses it under the hood, which Groq's hosted Llama models and OpenAI's
    models both provide."""
    if settings.judge_provider == "groq":
        return ChatGroq(
            model=settings.judge_model or _DEFAULT_GROQ_JUDGE_MODEL,
            api_key=SecretStr(settings.groq_api_key or ""),
        )
    if settings.judge_provider == "openai":
        return ChatOpenAI(
            model=settings.judge_model or _DEFAULT_OPENAI_JUDGE_MODEL,
            api_key=SecretStr(settings.openai_api_key or ""),
        )
    return ChatAnthropic(
        model_name=settings.judge_model or settings.agent_model,
        api_key=SecretStr(settings.anthropic_api_key or ""),
        timeout=None,
        stop=None,
    )


def resolved_judge_model_label(settings: Settings) -> str:
    """The actual model name the judge will run, given
    settings.judge_provider — same resolution _build_judge_llm uses."""
    if settings.judge_provider == "groq":
        return settings.judge_model or _DEFAULT_GROQ_JUDGE_MODEL
    if settings.judge_provider == "openai":
        return settings.judge_model or _DEFAULT_OPENAI_JUDGE_MODEL
    return settings.judge_model or settings.agent_model


def _build_judge_node(settings: Settings):
    judge_llm = _build_judge_llm(settings).with_structured_output(JudgeVerdict)  # binds the contract to the verdict
    judge_model_label = resolved_judge_model_label(settings)

    async def judge(state: AgentState, config: RunnableConfig) -> dict:
        attempts = state.get("judge_attempts", 0) + 1

        # One span per attempt, nested under agent_executor.py's
        # a2a.task_execute span via OTel's ambient context — this is what
        # lets the diagram/telemetry panel show pass vs. fail per attempt,
        # not just a single opaque "judge ran" marker. `judge.status` is
        # deliberately set FIRST among the per-attempt attributes (right
        # after `judge.attempt`, before reason/provider/max_attempts) —
        # StoryNode.tsx only previews a matched span's first 3 attributes,
        # and pass/fail is the one this feature exists to surface there.
        with with_span("judge.evaluate", {"judge.attempt": attempts}) as span:
            if attempts > settings.judge_max_attempts:
                logger.info("judge: gave up after %d attempts, returning last answer as-is", attempts - 1)
                span.set_attribute("judge.status", "gave_up")
                span.set_attribute("judge.reason", f"exceeded judge_max_attempts ({settings.judge_max_attempts})")
                span.set_attribute("judge.provider", settings.judge_provider)
                span.set_attribute("judge.model", judge_model_label)
                return {"judge_attempts": attempts, "judge_status": "gave_up"}

            proposed_answer = _extract_text(state["messages"][-1].content)
            try:
                verdict = await judge_llm.ainvoke(
                    [
                        _JUDGE_SYSTEM,
                        HumanMessage(
                            content=f"REQUEST:\n{state.get('delegated_request', '')}\n\nPROPOSED ANSWER:\n{proposed_answer}"
                        ),
                    ],
                    config=config,
                )
            except Exception:
                # Fail OPEN, not closed — unlike every security check in this
                # app, a broken judge call shouldn't block a real answer from
                # reaching the user. Deliberately the opposite failure
                # direction from anything auth-related. Not a span-level
                # error either, for the same reason — see the note on
                # verdict.status below.
                logger.exception("judge: evaluation call failed, passing answer through")
                span.set_attribute("judge.status", "pass")
                span.set_attribute("judge.reason", "evaluation call failed — fail-open")
                span.set_attribute("judge.provider", settings.judge_provider)
                span.set_attribute("judge.model", judge_model_label)
                return {"judge_attempts": attempts, "judge_status": "pass"}

            assert isinstance(verdict, JudgeVerdict)
            logger.info("judge: attempt=%d status=%s reason=%s", attempts, verdict.status, verdict.reason)

            # judge.status="fail" is a normal business outcome, not a span
            # execution failure — deliberately NOT calling
            # span.set_status(ERROR) here, so a red span in this panel
            # always means the judge call itself broke, not that it did its
            # job and said no.
            span.set_attribute("judge.status", verdict.status)
            span.set_attribute("judge.reason", verdict.reason)
            span.set_attribute("judge.provider", settings.judge_provider)
            span.set_attribute("judge.model", judge_model_label)
            span.set_attribute("judge.missing_info_count", len(verdict.missing_info))

            if verdict.status == "fail":
                feedback = f"Your previous answer didn't fully satisfy the request: {verdict.reason}"
                if verdict.missing_info:
                    feedback += f" Missing: {', '.join(verdict.missing_info)}."
                feedback += " Please address this and try again."
                return {
                    "messages": [HumanMessage(content=feedback)],
                    "judge_attempts": attempts,
                    "judge_status": "fail",
                }

            return {"judge_attempts": attempts, "judge_status": "pass"}

    return judge


def _judge_routing(state: AgentState) -> str:
    return "retry" if state.get("judge_status") == "fail" else "done"


async def build_graph(settings: Settings) -> CompiledStateGraph:
    tools = await _get_mcp_tools(settings)
    actor_cache: dict[str, Any] = {}

    async def _wrap(request: ToolCallRequest, execute):
        return await _scoped_tool_call(settings, actor_cache, request)

    graph = StateGraph(AgentState)
    graph.add_node("task_assistant", _build_task_assistant_node(settings, tools))
    graph.add_node("execute_tool", ToolNode(tools, awrap_tool_call=_wrap))
    graph.set_entry_point("task_assistant")

    if settings.judge_enabled:
        graph.add_node("judge", _build_judge_node(settings))
        graph.add_conditional_edges("task_assistant", tools_condition, {"tools": "execute_tool", END: "judge"})
        graph.add_conditional_edges("judge", _judge_routing, {"retry": "task_assistant", "done": END})
        graph.add_edge("execute_tool", "task_assistant")
        return graph.compile()

    graph.add_conditional_edges("task_assistant", tools_condition, {"tools": "execute_tool", END: END})
    graph.add_edge("execute_tool", "task_assistant")
    return graph.compile()
