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
  `agentorchestration_shared.verify_bearer_token`, fails the A2A task
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
bound via `bind_tools()`) ↔ `tools`
(`ToolNode([ask_task_agent_read, ask_task_agent_write])`). Two tools, not
one with a read/write parameter — the model's own tool *choice* is the
read/write signal. The `assistant` → `tools` → `assistant` → `END` loop is
standard `langgraph.prebuilt` usage; nothing bespoke.

**Task Agent** (`task-agent/app/graph.py`, separate process, own A2A
server) — `task_assistant` (Claude, tools bound from
`langchain_mcp_adapters.MultiServerMCPClient({...}).get_tools()`) ↔
`execute_tool` (`ToolNode(tools, awrap_tool_call=_policy_wrap_tool_call)`).

**The actual cross-process hop**: `backend/app/agent/tools.py`'s
`ask_task_agent_read`/`ask_task_agent_write` — thin `@tool async def`
wrappers (not graph nodes themselves; they're what `ToolNode` executes)
around a shared `_delegate(request, config, *, scope)` helper. Each reads
its own scope's token from `config["configurable"]["bearer_tokens"][scope]`
(a dict keyed by scope, not a single token) — injected by giving the tool
function a `config: RunnableConfig` parameter, which LangChain
auto-populates and hides from the tool's schema — and makes a *real* A2A
client call via `a2a.client.create_client(...)`. Not an in-process function
call to anything in `task-agent/`.

### Per-action scoped delegation (why this is a stronger demo than it looks)

There is no single "the agent is authenticated" state. Delegation is scoped
per action (`todos:read` / `todos:write` — see `Settings.todos_read_scope`
/ `todos_write_scope`), each approved independently via its own RFC 8693
Token Exchange (`POST /api/auth/agent-token {"scope": "..."}`,
`backend/app/auth/routes.py`), the first time that specific scope is
needed. `backend/app/auth/session.py`'s `EXCHANGED_TOKEN_COOKIE` holds a
**dict keyed by scope**, not one token — approving `todos:read` never
grants `todos:write`.

When a tool needs a scope with no entry in that dict yet, it returns
`NEEDS_AGENT_AUTH_MARKER` (`backend/app/agent/tools.py`); `routes/invoke.py`
maps the firing tool name back to its required scope and emits an
`auth_required` SSE event carrying `{"scope": "..."}`. The frontend's
`InlineAgentApprovalPrompt.tsx` renders off that event, scoped to exactly
that one action.

The Chat Agent forwards the delegated token for whichever scope a tool
needs — never a new/nested token, and never a token for a *different*
scope than what's needed. The Task Agent independently re-verifies both the
token **and its `scope` claim** (`shared/inbound_auth.VerifiedIdentity.scope`,
checked in `task-agent/app/policy.py`) before it will act on a given tool —
it extends zero implicit trust to the Chat Agent's own prior verification
*or* to whichever token the Chat Agent happened to attach. Verified this
holds adversarially: a validly-signed, correctly-audienced token scoped
only `todos:read`, sent directly to the Task Agent bypassing the Chat
Agent's own tool availability, asking it to write, was independently
rejected with no mutation.

`backend/.env`, `task-agent/.env`, and `mcp-todos-server/.env`'s
`OIDC_DISCOVERY_URL`, `AGENT_EXPECTED_AUDIENCE`, `TODOS_READ_SCOPE`, and
`TODOS_WRITE_SCOPE` must all match exactly for this to work — the
delegated token is forwarded unchanged through both hops and each service
independently re-verifies it; a mismatch fails with a genuinely correct
`audience_mismatch`/`issuer_mismatch`, not a bug. `mcp-todos-server` also
independently re-checks policy (its own `app/policy.py`, same shape as
task-agent's) rather than trusting that task-agent already gated the
call — every call, allowed or denied, is written to its OBO audit log
(`mcp-todos-server/app/audit.py`).

A real next step, not yet built: the Task Agent expecting a *narrower*,
distinct audience of its own (on top of the scope check it already does),
which would require the Chat Agent to do a second RFC 8693 token exchange
before delegating (true nested/rescoped delegation, rather than forwarding
the same-audience token for every scope).

### Extending with a third tool or a third agent

- **New mocked tool on the Task Agent**: add it to
  `mcp-todos-server/app/mcp_server.py` as another `@mcp.tool` function
  (verify the caller with `_authorize(tool_name)`, then call `_record(...)`
  on success — copy the shape of `list_todos`/`add_todo`/`complete_todo`
  exactly), then add entries to **both** `task-agent/app/policy.py`'s and
  `mcp-todos-server/app/policy.py`'s `_identity_acl()` and
  `_required_scope()` (pick whichever of `todos_read_scope`/
  `todos_write_scope` fits, or a new scope entirely if it's a genuinely
  different capability) — each service's gate is independent, update both
  or the new tool is quietly rejected at the second hop even once the Task
  Agent allows it. Nothing else changes —
  `MultiServerMCPClient.get_tools()` picks it up automatically.
- **New tool/capability on the Chat Agent**: add another `@tool async def`
  in `backend/app/agent/tools.py` (following the `_delegate(..., scope=...)`
  pattern if it needs request-scoped credentials), add it to the `_TOOLS`
  list in `backend/app/agent/graph.py`. If it needs a new scope, add it to
  `Settings.allowed_delegation_scopes` (`backend/app/config.py`) — `/api/auth/agent-token`
  refuses to exchange for anything not in that set.
- **A third agent**: give it its own directory (sibling to `task-agent/`),
  its own `AgentCard`/`AgentExecutor`/graph following `task-agent/app/`'s
  shape exactly, and its own inbound-auth check calling
  `agentorchestration_shared.verify_bearer_token` — don't reimplement verification.
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
  invocation (e.g. the verified `client_id` *and* `granted_scope` for the
  policy check).
- **`AIMessage.content` is `str | list[dict]`**, not always a plain
  string — normalize with an `_extract_text()` helper (see both
  `backend/app/routes/invoke.py` and `task-agent/app/agent_executor.py`)
  before passing it anywhere that needs a real `str`, e.g.
  `a2a.helpers.new_text_part(text=...)`, which raised an opaque
  `TypeError: bad argument type for built-in operation` when handed a list.

## Honest framing if asked "why LangChain for this"

At the two-node stage (as of 2026-08-15), LangGraph is earning its keep for
real now, not just "on credit" — the `ToolNode`/`tools_condition`
prebuilt loop, `awrap_tool_call` for the policy gate, and
`RunnableConfig`-based credential threading are all things that would've
been meaningfully more code to hand-roll. That wasn't true back when there
was only one node calling Claude directly; don't retroactively claim it
was.
