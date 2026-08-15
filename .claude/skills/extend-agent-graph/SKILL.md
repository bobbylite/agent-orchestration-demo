---
name: extend-agent-graph
description: Conventions for the LangGraph graphs in backend/app/agent/ and task-agent/app/, and the real A2A wiring between them. Read this before adding any node, tool, or a third agent — the pattern to copy is already built, don't re-derive it.
---

# Extending the agent graph(s)

## The one rule that overrides everything else here

**Security and authorization logic never lives inside a LangGraph node.**
It is always a deterministic gate that runs before the graph is touched —
a FastAPI dependency, an `AgentExecutor.execute()` check before the graph
is invoked, or a `ToolNode` `awrap_tool_call` hook — never something
reachable via the graph's own control flow or an LLM's judgment.

Why this is non-negotiable: a graph node's execution is something the
model's own output can influence (which node runs next, whether a tool
gets called, with what arguments). Anything that decides *whether the
caller is allowed to be here at all* must not be inside that blast radius.

Two worked examples already in this repo, copy their shape for anything new:
- **Request-level inbound auth**: `backend/app/routes/invoke.py` calls
  `verify_inbound_token()` *before* `graph.astream_events(...)`.
  `task-agent/app/agent_executor.py`'s `TaskAgentExecutor.execute()` does
  the same thing before `graph.ainvoke(...)` — pulls the bearer token from
  `context.call_context.state["headers"]`, verifies via
  `agentcore_shared.verify_bearer_token`, fails the A2A task
  (`task_updater.failed(...)`) before the graph runs if it doesn't check out.
- **Tool-level policy ACL**: `task-agent/app/policy.py`'s `check()` is a
  plain dict lookup, wired into `task-agent/app/graph.py`'s `ToolNode` via
  the `awrap_tool_call` hook — checked per tool call, before the MCP server
  is ever touched. A denied call returns a `ToolMessage` explaining why, so
  the model can tell the user, rather than silently failing or crashing.

If you add a third check (rate limiting, a broader ACL, whatever), it
follows one of these two shapes: request-level (before the graph starts) or
per-tool-call (via `awrap_tool_call`). Don't invent a third pattern, and
don't give either check its own LLM call unless there's a genuine need for
model judgment (there wasn't for either of the two built here).

## Current state — two graphs, two processes, real A2A between them

Same two-node ReAct loop shape in both (prebuilt `ToolNode` +
`tools_condition`, not hand-rolled):

**Chat Agent** (`backend/app/agent/graph.py`) — `assistant` (Claude, tools
bound via `bind_tools()`) ↔ `tools` (`ToolNode([ask_task_agent])`). The
`assistant` → `tools` → `assistant` → `END` loop is standard
`langgraph.prebuilt` usage; nothing bespoke.

**Task Agent** (`task-agent/app/graph.py`, separate process, own A2A
server) — `task_assistant` (Claude, tools bound from
`langchain_mcp_adapters.MultiServerMCPClient({...}).get_tools()`) ↔
`execute_tool` (`ToolNode(tools, awrap_tool_call=_policy_wrap_tool_call)`).

**The actual cross-process hop**: `backend/app/agent/tools.py`'s
`ask_task_agent` — an `@tool async def` (not a graph node itself; it's what
`ToolNode` executes) that reads `bearer_token`/`task_agent_url` off
`RunnableConfig["configurable"]` (injected by giving the tool function a
`config: RunnableConfig` parameter — LangChain auto-injects it and hides it
from the tool's schema) and makes a *real* A2A client call via
`a2a.client.create_client(...)`. Not an in-process function call to
anything in `task-agent/`.

### Identity propagation (why this is a stronger demo than it looks)

The Chat Agent forwards the *same* delegated (RFC 8693 exchanged) token it
already verified for its own `/api/invoke` inbound auth — threaded through
via `config["configurable"]["bearer_token"]`, set in
`backend/app/routes/invoke.py`. No new/nested token is minted. The Task
Agent independently re-verifies that same token (same
`agentcore_shared.verify_bearer_token`, same issuer/audience) before it
will act — it extends zero implicit trust to the Chat Agent's own prior
verification. `backend/.env` and `task-agent/.env`'s `OIDC_DISCOVERY_URL`
and `AGENT_EXPECTED_AUDIENCE` must match exactly for this to work; a
mismatch fails with a genuinely correct `audience_mismatch`/
`issuer_mismatch`, not a bug.

A real next step, not yet built: the Task Agent expecting a *narrower*,
distinct audience of its own, which would require the Chat Agent to do a
second RFC 8693 token exchange before delegating (true nested/rescoped
delegation, rather than forwarding the same token to both hops).

### Extending with a third tool or a third agent

- **New mocked tool on the Task Agent**: add it to
  `mcp-todos-server/server.py` as another `@mcp.tool` function, then add an
  entry to `task-agent/app/policy.py`'s `TOOL_ACL` dict. Nothing else
  changes — `MultiServerMCPClient.get_tools()` picks it up automatically.
- **New tool/capability on the Chat Agent**: add another `@tool async def`
  in `backend/app/agent/tools.py` (following `ask_task_agent`'s
  `RunnableConfig`-injection pattern if it needs request-scoped
  credentials), add it to the `_TOOLS` list in `backend/app/agent/graph.py`.
- **A third agent**: give it its own directory (sibling to `task-agent/`),
  its own `AgentCard`/`AgentExecutor`/graph following `task-agent/app/`'s
  shape exactly, and its own inbound-auth check calling
  `agentcore_shared.verify_bearer_token` — don't reimplement verification.
  Whether it shares the Chat Agent's audience or gets a distinct one
  (requiring a fresh token exchange) is the same open question as above.

## Verified library APIs (don't re-derive from docs, they were wrong once)

Confirmed by installing into a throwaway venv and introspecting real
signatures — the web-summarized SDK docs for `a2a-sdk` were stale/wrong
about some class locations (e.g. described `a2a.server.apps`, which doesn't
exist in `a2a-sdk` 1.1.2; it's `a2a.server.routes`).

- **A2A server**: `a2a.types.{AgentCard, AgentSkill, AgentCapabilities,
  AgentInterface}` for the card; `a2a.server.agent_execution.AgentExecutor`
  subclass with `execute(context, event_queue)`/`cancel(...)`;
  `a2a.server.tasks.TaskUpdater` (`.start_work()`, `.failed()`,
  `.complete()`, `.add_artifact()`) for task lifecycle; wire into FastAPI
  via `a2a.server.routes.add_a2a_routes_to_fastapi(app,
  agent_card_routes=create_agent_card_routes(card),
  jsonrpc_routes=create_jsonrpc_routes(handler, "/"))`, built inside the
  app's `lifespan` (not at import time — building the graph needs `await`).
- **A2A client**: `a2a.client.A2ACardResolver(httpx_client=...,
  base_url=...).get_agent_card()`, then
  `a2a.client.create_client(agent=card, client_config=ClientConfig(streaming=False,
  httpx_client=...))`, `client.send_message(SendMessageRequest(message=new_text_message(...)))`.
  Responses are **protobuf** (`a2a_pb2.StreamResponse`) — check
  `chunk.HasField("task")`, extract via
  `chunk.task.artifacts[-1].parts[-1].text`, not dict-style access.
  Attaching a bearer token: pass a `headers={"Authorization": f"Bearer
  {token}"}` httpx.AsyncClient into both the resolver and `ClientConfig`.
- **MCP server**: `fastmcp.FastMCP("name")`, `@mcp.tool` decorator,
  `mcp.run(transport="http", host=..., port=..., path="/mcp")`.
- **LangGraph ↔ MCP**: `langchain_mcp_adapters.client.MultiServerMCPClient({"name":
  {"transport": "streamable_http", "url": "..."}}).get_tools()` returns
  ready-to-bind LangChain `BaseTool` objects — this is genuinely all that's
  needed, no manual MCP protocol handling.
- **Per-tool-call middleware**: `langgraph.prebuilt.ToolNode(tools,
  awrap_tool_call=handler)` where `handler(request: ToolCallRequest,
  execute) -> ToolMessage | Command` — `request.runtime.config["configurable"]`
  is how the handler reads values threaded in from the outer graph
  invocation (e.g. the verified `client_id` for the policy check).

## Honest framing if asked "why LangChain for this"

At the two-node stage (as of 2026-08-15), LangGraph is earning its keep for
real now, not just "on credit" — the `ToolNode`/`tools_condition`
prebuilt loop, `awrap_tool_call` for the policy gate, and
`RunnableConfig`-based credential threading are all things that would've
been meaningfully more code to hand-roll. That wasn't true back when there
was only one node calling Claude directly; don't retroactively claim it
was.
