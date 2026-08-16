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
`langchain_mcp_adapters.MultiServerMCPClient({...}).get_tools()`, fetched
once per task **purely for schemas** — `tools/list` needs no auth) ↔
`execute_tool` (`ToolNode(tools, awrap_tool_call=_wrap)`, where `_wrap`
calls `_scoped_tool_call` — full custom dispatch, not just a gate: it
performs this service's own delegation chain and builds a
freshly-authorized MCP connection per call, rather than executing the
schema-only tool objects directly).

**The actual cross-process hop**: `backend/app/agent/tools.py`'s
`ask_task_agent_read`/`ask_task_agent_write` — thin `@tool async def`
wrappers (not graph nodes themselves; they're what `ToolNode` executes)
around a shared `_delegate(request, config, *, intent)` helper. Both read
the *same* single delegation token from
`config["configurable"]["delegated_token"]` — injected by giving the tool
function a `config: RunnableConfig` parameter, which LangChain
auto-populates and hides from the tool's schema — and make a *real* A2A
client call via `a2a.client.create_client(...)`. Not an in-process function
call to anything in `task-agent/`. `intent` ("read"/"write") is telemetry
labeling only now — it no longer selects a different OAuth scope at this
layer.

### Chained RFC 8693 delegation (why this is a stronger demo than it looks)

There is no single "the agent is authenticated" state, and — since the
2026-08-16 redesign — no single token forwarded unchanged through every
hop either. Each service that acts mints its own credential, right before
it acts, scoped to exactly what it's about to do:

- **Chat Agent**: Client Credentials (own identity, scope
  `agent_own_scope`, a PingOne app dedicated to this) → RFC 8693 Token
  Exchange (`POST /api/auth/agent-token`, no request body — one approval
  per session now, not per action) using a **different** PingOne app,
  scope always `agent_delegation_scope` (generic — never
  action-specific). `backend/app/auth/session.py`'s `EXCHANGED_TOKEN_COOKIE`
  holds a single token now, not a dict keyed by scope.
- **Task Agent**: verifies that inbound token (audience = its own URL,
  scope must contain `agent_delegation_scope`) → Client Credentials (own
  identity, scope `agent_task_scope`, cached per task) → RFC 8693 Token
  Exchange **fresh, every tool call** (`subject_token` = the token it
  received from the Chat Agent, `actor_token` = its own, scope =
  `todos_read_scope` or `todos_write_scope` depending on which specific
  tool is about to run) — this is where per-action scoping actually lives
  now, and it's what gives step-up scoping for free: nothing about which
  scope was granted is cached across different tool calls, so a `todos:read`
  succeeding earlier has no bearing on whether a later `todos:write`
  attempt succeeds.

When the Chat Agent has no delegation credential yet, `_delegate()`
returns `NEEDS_AGENT_AUTH_MARKER`; `routes/invoke.py` emits a generic
`auth_required` SSE event (no scope payload anymore — there's only one
thing to approve at this layer). The frontend's
`InlineAgentApprovalPrompt.tsx` renders off that event.

Each service independently re-verifies whatever it receives — zero
implicit trust in a previous hop's say-so. Verified this holds
adversarially, both before and after the redesign: a validly-signed,
correctly-audienced token scoped only `todos:read`, sent directly to
whichever service is the real enforcement point, asking it to write, was
independently rejected with no mutation.

**Critical claim gotcha, confirmed via real PingOne tokens 2026-08-16**:
every policy ACL (`task-agent/app/policy.py`, `mcp-todos-server/app/policy.py`)
must check `VerifiedIdentity.agent_client_id` — the custom claim PingOne
propagates from whichever *actor* token fed the exchange that produced the
token being verified (i.e. which agent is actually delegating) — **not**
`.client_id`, which is just whichever PingOne app happened to authenticate
that specific exchange call. Both policy files had this backwards briefly
before real tokens caught it; see `CLAUDE.md`'s "RFC 8693 chained
delegation" section for the full worked example.

`task-agent/.env` and `mcp-todos-server/.env`'s `OIDC_DISCOVERY_URL` must
match (same PingOne tenant); their `AGENT_EXPECTED_AUDIENCE` values must
**NOT** match each other — each is that service's own URL now, not a
shared value. `TODOS_READ_SCOPE`/`TODOS_WRITE_SCOPE` must match between
just those two services (`backend/.env` doesn't have these settings at
all anymore). `mcp-todos-server` independently re-checks policy (its own
`app/policy.py` — unlike task-agent's copy, it *does* still check scope,
since the token it receives genuinely carries `todos:read`/`todos:write`)
rather than trusting that task-agent already gated the call — every call,
allowed or denied, is written to its OBO audit log
(`mcp-todos-server/app/audit.py`).

The "Task Agent expecting a narrower, distinct audience, requiring a
second token exchange" next-step mentioned in earlier versions of this
doc is now **built** — that's exactly what the chain above is.

### Extending with a third tool or a third agent

- **New mocked tool on the Task Agent**: add it to
  `mcp-todos-server/app/mcp_server.py` as another `@mcp.tool` function
  (verify the caller with `_authorize(tool_name)`, then call `_record(...)`
  on success — copy the shape of `list_todos`/`add_todo`/`complete_todo`
  exactly), then: (1) add an identity ACL entry to
  `mcp-todos-server/app/policy.py`'s `_identity_acl()`, and (2) add an
  entry to `task-agent/app/graph.py`'s `_REQUIRED_SCOPE_SETTING` map
  (which `Settings` attribute names the scope task-agent's own exchange
  should request for this tool — pick `todos_read_scope`/
  `todos_write_scope` or add a new scope entirely for a genuinely
  different capability). Each service's gate is independent — miss either
  and the new tool is quietly rejected at that hop even once the others
  allow it. Nothing else changes — `MultiServerMCPClient.get_tools()`
  picks it up automatically for schema purposes.
- **New tool/capability on the Chat Agent**: add another `@tool async def`
  in `backend/app/agent/tools.py` (following the `_delegate(..., intent=...)`
  pattern), add it to the `_TOOLS` list in `backend/app/agent/graph.py`.
  The Chat Agent doesn't track per-capability scopes itself anymore — every
  tool shares the same delegation credential; the Task Agent decides the
  actual downstream scope, per call, when it performs its own exchange.
- **A third agent**: give it its own directory (sibling to `task-agent/`),
  its own `AgentCard`/`AgentExecutor`/graph following `task-agent/app/`'s
  shape exactly, and its own inbound-auth check calling
  `agentorchestration_shared.verify_bearer_token` — don't reimplement
  verification. Give it its own PingOne worker-app identity and its own
  RFC 8693 exchange for whatever resource it actually touches, mirroring
  `task-agent/app/graph.py`'s `_scoped_tool_call` — don't have it just
  forward an upstream token unchanged; that was the pre-2026-08-16 design.

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
  invocation. The `execute` callback is optional to actually call —
  `task-agent/app/graph.py`'s `_scoped_tool_call` never calls it; it does
  full custom dispatch instead (its own token exchange, then a freshly-built
  MCP connection), which is a legitimate use of the hook, not a workaround.
- **Custom claims on an exchanged token aren't just the standard OAuth
  ones** — a real PingOne Token Exchange response carries a custom
  `agent_client_id` claim (propagated from the actor token) alongside the
  standard top-level `client_id` (whichever app authenticated *that*
  request). Don't assume a claim name from the RFC text; decode a real
  token before writing a policy check against a new one.
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
