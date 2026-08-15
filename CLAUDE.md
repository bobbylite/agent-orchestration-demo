# Agent Orchestration Console (LangGraph edition)

A PingOne-authenticated chat console for a LangGraph agent. Python/FastAPI +
React rebuild of [digital-assistant-demo](https://github.com/bobbylite/digital-assistant-demo),
with AWS Bedrock AgentCore replaced by a local LangGraph graph, deliberately
built to mirror AgentCore's *inbound auth* security model rather than a
simpler session-based one.

See `README.md` for setup/run instructions and PingOne app configuration.
This file is for conventions, decisions, and in-progress/shelved plans a
future session (or future you) needs to not re-derive from scratch.

## Stack

```
backend/                     Chat Agent — FastAPI + LangGraph + OpenTelemetry, Python 3.14 via uv
frontend/                    React 19 + Vite 8 (rolldown) + Tailwind v4, Node >=22.12
task-agent/                  Specialist Agent — separate process, own A2A server (a2a-sdk)
mcp-todos-server/            MCP server (fastmcp, Streamable HTTP, mocked todos tool) + FastAPI app
                              for its own PingOne-gated UI's API + OBO audit log
mcp-todos-server/frontend/   React 19 + Vite 8 (rolldown) + Tailwind v4 — its own UI
shared/                      agentorchestration_shared — inbound-auth verification, used by
                              backend + task-agent + mcp-todos-server
```

Everything was deliberately scaffolded on latest-stable versions, not
conservative defaults — that's a standing preference, not a one-off.
`shared/` is a real `uv` package, installed editable into both `backend/`
and `task-agent/` via `[tool.uv.sources] = { path = "...", editable = true }`
— NOT a `uv` workspace (each service keeps its own independent `.venv`;
a full workspace merges venvs and would've changed the "run from this
service's own directory" convention everywhere).

## The core architectural decision: auth is never inside the graph

Every security/authorization check in this app is a deterministic gate that
runs **before** any LangGraph node executes — never as a graph node itself,
and never something an LLM's output influences. This is not a minor style
choice; it's the load-bearing decision behind the whole backend structure:

- `backend/app/auth/inbound.py` (`verify_inbound_token`) re-verifies the
  bearer token *fresh on every call* — signature against PingOne's JWKS,
  issuer, expiry, and **audience** — rather than trusting a session.
- This intentionally mirrors AWS Bedrock AgentCore's inbound-auth model: a
  JWT authorizer sitting in front of the runtime, stateless, re-verified per
  request — not something the agent framework (LangGraph, in either case)
  implements or is trusted to enforce.

**Two privilege tiers, not one blanket gate (2026-08-15).** Plain chat
never touches a protected resource, so the signed-in session cookie —
already fully verified once, at OIDC login — is sufficient on its own;
`/api/invoke` (`backend/app/routes/invoke.py`) doesn't require an exchanged
token to answer at all. A delegated token *is* still required, and still
independently re-verified fresh via inbound auth, the moment the agent
needs to act **on the user's behalf**.

**Per-action scoped delegation, not one token for everything (revised
2026-08-15).** There is no single "authenticated" state for the agent —
delegation is scoped per action, approved just-in-time, the first time each
scope is actually needed:

- Two tools, not one: `ask_task_agent_read` (needs `todos:read`) and
  `ask_task_agent_write` (needs `todos:write`) — `backend/app/agent/tools.py`.
  The model's own tool choice *is* the read/write signal; there's no
  keyword classifier deciding this.
- Each scope gets its own independent RFC 8693 Token Exchange call
  (`POST /api/auth/agent-token` with `{"scope": "todos:read"}` or
  `"todos:write"`) — approving one never grants the other. The exchanged
  token cookie is a dict keyed by scope (`{"todos:read": {...}, "todos:write":
  {...}}`), not a single token.
- When a tool needs a scope that isn't in that dict yet, it returns a
  sentinel (`NEEDS_AGENT_AUTH_MARKER`) that `routes/invoke.py` maps back to
  the *specific* scope (via which tool fired) and turns into an
  `auth_required` SSE event carrying `{"scope": "..."}`. The frontend
  renders `InlineAgentApprovalPrompt.tsx`, scoped to that one action,
  labeled "Approve Agent Action" — not a generic "Authenticate Agent"
  button (that button and its header placement are gone entirely; approval
  is purely contextual now, there's no coherent generic action left to
  attach a standalone button to).
- Approving stacks, it doesn't replace: after approving read then write,
  both scopes are present and both kinds of request succeed without
  re-approving.

**The scoping is enforced, not just labeled in the UI** — this is the part
that would be easy to skip and shouldn't be. `task-agent/app/policy.py`'s
`check()` takes the verified token's own `scope` claim (not just identity)
and requires the specific scope each tool needs (`list_todos`→read,
`add_todo`/`complete_todo`→write) via `TaskAgentExecutor` → the `ToolNode`
`awrap_tool_call` hook. I verified this holds even adversarially: a
validly-signed, correctly-audienced token scoped *only* `todos:read`,
presented directly to the Task Agent (bypassing the Chat Agent's own tool
availability entirely) asking it to add a todo, was independently rejected
— confirmed no mutation occurred. `shared/inbound_auth.VerifiedIdentity`
carries the raw `scope` claim (`.has_scope(required)` helper) for exactly
this.

None of this weakens the AgentCore-inbound-auth story — the actual
enforcement boundary (the Task Agent, where a protected resource is
touched) is untouched in spirit, just sharper: it hard-rejects any call
without a correctly-scoped, freshly-verified delegated token, independent
of whatever the Chat Agent believes, now down to the individual capability
rather than an all-or-nothing "authenticated."

**Rule for any future work**: if you're adding an authorization/policy
check, it is a plain function/FastAPI dependency, not a graph node — even if
it gets a persona-like name ("policy agent") in telemetry or UI copy for
storytelling purposes. `task-agent/app/policy.py`'s `check()` (identity ACL
*and* scope claim, both required), called from `TaskAgentExecutor.execute()`
(via `ToolNode`'s `awrap_tool_call` hook) before the MCP server is ever
touched, is the worked example — copy that shape, don't reinvent it.

Inbound-auth verification itself is shared, not reimplemented per service:
`shared/src/agentorchestration_shared/inbound_auth.py`'s `verify_bearer_token()` is
used by both `backend/app/auth/inbound.py` (thin `Settings`-aware adapter)
and `task-agent/app/agent_executor.py` (calls it directly). If you touch
verification logic, change it there once — don't patch either call site
independently, that's exactly the drift risk sharing it was meant to avoid.

## OpenTelemetry is the product, not an add-on

The user's stated goal: the Telemetry panel is how they narrate what's
happening security-wise, live, in a demo. Consequences for how you touch
this code:

- `backend/app/telemetry.py`: `RecordingSpanProcessor` — 300-span in-memory
  ring buffer + a redaction filter (`token|secret|password|authoriz` in the
  attribute *key*, case-insensitive) applied in `on_end()`, so it's
  impossible for a caller to accidentally leak a credential onto a span even
  if they try.
- Every credential-bearing step gets its own named span:
  `oidc.login.redirect`, `oidc.login.callback`, `oidc.logout`,
  `agent.authenticate` → children `agent.client_credentials` +
  `agent.token_exchange`, `inbound_auth.verify`, `agent.invoke`.
- `inbound_auth.verify` is deliberately its own top-level span, not nested
  under `agent.invoke` — it represents a distinct phase (the gate) that
  happens *before* the agent runtime starts, same as it would in AgentCore.
- When adding a span via `with_span(name, attributes)`
  (`backend/app/telemetry.py`), don't call `span.record_exception`/
  `set_status` yourself *and* let the exception propagate through the `with`
  block — `start_as_current_span` is called with
  `record_exception=False, set_status_on_exception=False` specifically so
  there's exactly one place recording each exception. Pick one.
- Frontend: spans are keyed by `span_id` in `TelemetryPanel.tsx`, so React
  only mounts (and animates in via `.animate-pop-in`) genuinely new spans on
  each 2s poll — don't break that keying, it's what makes the panel not
  flicker.

## LangGraph + real A2A: two graphs, two processes (built 2026-08-15)

Both graphs use the standard LangGraph ReAct loop shape (prebuilt
`ToolNode` + `tools_condition`): an assistant node reasons and optionally
emits a tool call, a tools node executes it, loop back to the assistant to
turn the result into a natural-language answer, then `END`.

**Chat Agent graph** (`backend/app/agent/graph.py`) — 2 nodes:
- `assistant` — Claude, with `ask_task_agent_read` and `ask_task_agent_write`
  (`backend/app/agent/tools.py`) bound via `bind_tools()`. Decides itself,
  via real tool-calling, whether to delegate *and which scope it needs* —
  nothing routes on keywords, and there's no single generic delegation tool.
- `tools` — a `ToolNode` wrapping both, which are *not* LLM calls: each is a
  real A2A client call (`a2a.client.create_client`) to the Task Agent, over
  HTTP/JSON-RPC, not an in-process function call. Each forwards the
  delegated token for its *own* scope specifically (see "Per-action scoped
  delegation" below) — threaded through via
  `config["configurable"]["bearer_tokens"]` (a dict keyed by scope), set in
  `backend/app/routes/invoke.py`.

**Task/Specialist Agent graph** (`task-agent/app/graph.py`, separate
process, own A2A server via `a2a-sdk`) — 2 nodes:
- `task_assistant` — its own Claude call, reasons about which MCP tool to
  invoke (`list_todos`/`add_todo`/`complete_todo`, fetched from
  `mcp-todos-server/` via `langchain_mcp_adapters.MultiServerMCPClient` and
  bound the same way as the Chat Agent's tools).
- `execute_tool` — a `ToolNode` over those MCP-backed tools, with the
  identity-*and*-scope policy check wired in via `awrap_tool_call` (see the
  core rule above) — denied calls return a `ToolMessage` explaining the
  denial, so the model can tell the user, rather than silently failing.

`TaskAgentExecutor.execute()` (`task-agent/app/agent_executor.py`) runs
inbound auth *before* invoking this graph at all — pulls the bearer token
out of `context.call_context.state["headers"]`, verifies it, and only then
calls `graph.ainvoke(...)`, passing the verified `client_id` *and*
`identity.scope` through `config["configurable"]["client_id"]` /
`["granted_scope"]` for the policy check to use.

### Identity propagation across the A2A hop

The Chat Agent does **not** mint a new/nested token for the Task Agent
call — it forwards the exact same RFC 8693 delegated token it already holds.
The Task Agent independently re-verifies that token itself (same
`agentorchestration_shared.verify_bearer_token`, same issuer/audience config,
duplicated across `backend/.env` and `task-agent/.env` on purpose — see
"Known gotchas"). This is deliberate: it proves the Task Agent trusts
nothing about the caller except a credential it can verify itself, which is
the actual property AgentCore-style inbound auth is for. A follow-up not
yet built: the Task Agent expecting a *different*, narrower audience,
requiring the Chat Agent to do a second token exchange before delegating.

### Verified end-to-end (2026-08-15)

Real signed JWTs throughout (RS256, self-issued during testing against a
throwaway mock OIDC/JWKS *and token* endpoint — full RFC 8693 exchange
calls actually executed, not mocked function calls). Two full passes:

- **Identity**: no token → task fails before the graph runs; forged
  signature → rejected; valid token from a client_id *not* in the ACL →
  tool call denied, model explains it to the user; valid token + allowed
  tool → real MCP data flows back through the A2A artifact into the Chat
  Agent's final answer. `a2a-sdk` also has its own OTel instrumentation
  (`a2a.client.transports.jsonrpc.*` spans) that flows into the same
  `RecordingSpanProcessor` ring buffer automatically, alongside
  `agent.a2a_delegate`, for free.
- **Scope** (the full read-then-write-needs-separate-approval flow): asking
  to read todos before any approval → `auth_required` for `todos:read`,
  graceful in-chat explanation, `exchanged_scopes: []`; approving read →
  real token exchange, retry succeeds with real MCP data; asking to
  complete a todo *with only read approved* → `auth_required` for
  `todos:write` specifically, **not** silently allowed by the existing read
  approval; approving write → both scopes now present; retry → real MCP
  mutation confirmed by reading the todo list back afterward
  (`done: true`). Adversarial check: a validly-signed, correctly-audienced
  token scoped *only* `todos:read`, sent directly to the Task Agent
  (bypassing the Chat Agent's own tool availability) asking it to add a
  todo, was independently rejected — confirmed no mutation occurred either.
  This test run also caught and fixed a real bug: `TaskAgentExecutor`
  assumed `AIMessage.content` was always a plain string when building the
  A2A artifact; it can be a list of content blocks depending on the
  response shape, which crashed `add_artifact`. Fixed with the same
  `_extract_text` normalization `routes/invoke.py` already used.

### Not yet built

- Cross-service telemetry aggregation — `task-agent/` has no
  `RecordingSpanProcessor`/`/telemetry` endpoint of its own yet; its
  activity isn't visible in the frontend's Telemetry panel, only via its
  own console/logs. The Chat Agent's `agent.a2a_delegate` span *does* show
  the delegation round-trip in the existing panel.
- `task-agent` having its own distinct PingOne identity (see "Identity
  propagation" above).

## MCP server: its own UI, PingOne SSO, and an OBO audit log (2026-08-15)

`mcp-todos-server/` stopped being a bare, unauthenticated `fastmcp`
one-file server (`server.py` is gone — replaced by an `app/` package) and
became a full FastAPI app that hosts three things on the same port (9000,
unchanged): the MCP endpoint (`/mcp`), a REST API for its own web UI
(`/api/auth`, `/api/todos`, `/api/audit`), and `/api/health`. The MCP
sub-app is built with `fastmcp`'s `mcp.http_app(path="/mcp")` and mounted
at `"/"` on the FastAPI app *after* all routers are included, so the
explicit `/api/*` routes match first and the mount only catches the MCP
traffic — and its `lifespan` is forwarded into FastAPI's own
(`FastAPI(lifespan=mcp_app.lifespan)`), or streamable-http sessions never
initialize.

**Why this exists**: the ask was to make the MCP server "agent aware" —
tag which todos a human created directly vs. an agent created on a human's
behalf — and, the important part, show a real OBO (on-behalf-of) audit
log: *"Agent Task Agent used list_todos on behalf of
robert.luisi@pingidentity.com"*. That requires the human's actual identity
to reach this service, which it never could before: task-agent only ever
saw a `sub` (opaque UUID) and no email/username claim, and had no way to
forward what it *did* verify any further downstream.

**Three PingOne apps now, not two.** `mcp-todos-server/`'s own UI gets a
dedicated third PingOne app (its own sign-in, own session cookie, own
`SESSION_SECRET`) — confirmed with the user rather than reusing app #1.
See README's "PingOne setup".

**Identity resolution for the audit log — confirmed design: token claim
first, session-cache fallback.** `shared/inbound_auth.VerifiedIdentity`
gained two additive fields: `email` (best-effort, from the verified
token's `email`/`preferred_username` claim — only present if the PingOne
resource maps one onto its access tokens) and `aud` (the audience actually
verified against — this is what lets the audit log literally show the
"(from audience)" detail the ask asked for). `mcp-todos-server/app/identity.py`
tries `VerifiedIdentity.email` first; if absent, falls back to a
`sub -> {email, name}` cache populated whenever a human signs into
*this service's own UI* (same PingOne tenant, so the same `sub` a
delegated token carries); if neither, callers show the raw `sub` rather
than fabricating a label. This means an OBO entry's human-readable
identity may show a raw `sub` for a human who has only ever used the chat
app and never separately signed into `mcp-todos-server/`'s UI — expected,
not a bug.

**task-agent now rebuilds its MCP connection per request, not once at
startup.** `langchain_mcp_adapters`' `StreamableHttpConnection` only
accepts `headers` at client-construction time, not per call, and the
whole point here is forwarding *this request's* freshly-verified bearer
token as the `Authorization` header on the MCP call so `mcp-todos-server`
can independently re-verify it (same shared `verify_bearer_token()`) and
attribute the call to the real human. So `task-agent/app/graph.py`'s
`build_graph()` now takes an optional `bearer_token`, and
`TaskAgentExecutor.execute()` (`task-agent/app/agent_executor.py`) calls
it fresh per task instead of `app/main.py`'s `lifespan` building one graph
reused forever. Deliberate trade: one extra MCP tool-list handshake per
task, in exchange for never forwarding a stale or wrong-identity
connection — same "verify/act fresh every time" principle as inbound auth
itself, not a performance oversight.

**`mcp-todos-server` gates independently of `task-agent`, not trusting its
gate.** `mcp-todos-server/app/policy.py` is a straight copy of
`task-agent/app/policy.py`'s shape (identity ACL + required scope, both
checked) — this service doesn't assume task-agent already enforced
anything, the same "adversarial" posture task-agent itself takes toward
the Chat Agent. A denied call still gets audited (`outcome="denied"`), not
silently dropped.

**Two actor paths into the same audit log**
(`mcp-todos-server/app/audit.py`, an in-memory ring buffer shaped like
`backend/app/telemetry.py`'s span buffer): `mcp-todos-server/app/mcp_server.py`'s
three tools record `actor_type="agent"` entries (the OBO ones — every
call, allowed or denied) via inbound-auth verification of the forwarded
token; `mcp-todos-server/app/routes/todos.py` (session-cookie
authenticated, same "a verified session is enough for a non-delegated
action" tier the chat app already uses for plain chat) records
`actor_type="human"` entries for direct UI actions. Both paths write
through the same `mcp-todos-server/app/store.py` functions, tagging every
todo `created_by: "human" | "agent"` plus who.

**A fourth (well, fifth counting both frontends) copy of the "must match
exactly" env-var gotcha**: `mcp-todos-server/.env`'s `OIDC_DISCOVERY_URL`,
`AGENT_EXPECTED_AUDIENCE`, `ALLOWED_AGENT_CLIENT_ID`, `TODOS_READ_SCOPE`,
and `TODOS_WRITE_SCOPE` must match `backend/.env`'s and `task-agent/.env`'s
values exactly — same reasoning as the existing gotcha below, now with a
third service in the chain. `ALLOWED_AGENT_CLIENT_ID` here is
`backend/.env`'s `AGENT_CLIENT_ID` (the Chat Agent's own worker-app client
id) — *not* task-agent's own identity, because the Chat Agent's delegated
token is forwarded unchanged all the way through, so its `client_id` claim
never changes hop to hop (see "Identity propagation across the A2A hop").

## What's built (as of 2026-08-15)

- Sign in with PingOne (OIDC Authorization Code + PKCE S256, JWE-encrypted
  session cookie) — sufficient on its own for plain chat.
- Per-action scoped delegation (Client Credentials + RFC 8693 Token
  Exchange, requested fresh per scope) — required only for the agent to act
  on the user's behalf (A2A delegation), enforced fresh via inbound auth at
  that point, not at the chat gate. See "Per-action scoped delegation" above.
- Inline in-chat approval: asking for something that needs a scope not yet
  granted gets a graceful explanation plus an `InlineAgentApprovalPrompt`
  scoped to that one action ("Approve Agent Action"), which auto-retries on
  success. There's no header-level "Authenticate Agent" button anymore —
  approval is purely contextual.
- OpenTelemetry spans + redaction + ring buffer, live in the Telemetry panel.
- LangGraph chat agent (Claude), streamed over SSE, Markdown-rendered,
  per-thread checkpointed — 2 nodes, with real, scope-gated A2A delegation
  to a separate Task Agent service for todos (see "LangGraph + real A2A"
  and "Per-action scoped delegation" above).
- `task-agent/` (own A2A server, own scope-aware policy gate) delegating to
  `mcp-todos-server/` — in-memory, no persistence, but no longer
  unauthenticated: its own PingOne-gated UI (todos tagged human vs. agent),
  its own scope-aware policy gate, and an OBO audit log. See "MCP server:
  its own UI, PingOne SSO, and an OBO audit log" above.
- UI styled to match Ping Identity's actual production site (pulled real
  hex values and Montserrat from their live CSS, not guessed): dark navy ink
  `#051727` on near-white `#fbfbfc`, red/orange brand gradient
  `#d84332→#d6311b→#d20e0f`, subtly-rounded (not pill) buttons. Dark mode
  reuses Ping's own dark navy shades (`#051727`/`#0f1b26`) as backgrounds.
- Light/dark theme toggle (`ThemeToggle.tsx` + `src/lib/theme.ts`) — the
  dark-mode CSS block in `index.css` **must** stay guarded as
  `:root:not([data-theme="light"])`; without that guard an explicit
  light-mode toggle silently does nothing on a system set to dark (this was
  a real bug, fixed once already — don't reintroduce it).
- Chat bubble + telemetry-span pop-in animation (`.animate-pop-in` in
  `index.css`), typing-dots indicator (`TypingIndicator.tsx`).

## Known gotchas (all previously hit — don't re-debug these)

- **`uv run` must be invoked from `backend/`**, not the repo root — it
  resolves against `backend/pyproject.toml`; wrong cwd fails with
  `Failed to spawn: uvicorn`.
- **Frontend needs Node >=22.12** — Vite 8's rolldown bundler silently fails
  to install its platform binary below that (`brew upgrade node`, or use a
  version manager).
- **`sse-starlette` frames SSE events with CRLF (`\r\n\r\n`)**, not bare
  `\n\n` — any raw SSE parsing code must normalize line endings before
  splitting on the blank-line separator, or events silently never parse
  (this broke the chat entirely once; fixed in `frontend/src/lib/api.ts`).
- **PingOne's token-exchange resource must issue JWT access tokens**, not
  opaque/reference tokens — inbound auth can only verify a JWT
  cryptographically. If `/api/invoke` 401s with `audience_mismatch`, the
  error message includes the actual `aud` value PingOne issued — set
  `AGENT_EXPECTED_AUDIENCE` to that value.
- **The *sign-in* app's own access token must ALSO be a JWT** — a separate
  gotcha from the one above, easy to conflate. `/api/auth/agent-token`
  sends `session["access_token"]` (minted at login, by app #1) as Token
  Exchange's `subject_token`; if the resource *that* app's tokens are
  minted against is opaque/reference (common PingOne default when no
  custom resource is attached — often the built-in "PingOne API" one),
  PingOne can't read claims off it and Token Exchange fails with `PingOne
  rejected the Token Exchange request ...: Cannot parse token claims for
  request param 'subject_token'` — a 502 from `backend/app/auth/routes.py`
  (which now surfaces PingOne's real `error_description` instead of
  crashing with a bare 500 — see below), not a silent failure. Fix is in
  PingOne's console (Connections → Resources → the resource → Access
  Token Type → JWT/self-signed), not code.
- **`backend/app/auth/routes.py`'s `/api/auth/agent-token` now converts
  `httpx.HTTPStatusError` from either PingOne call (Client Credentials,
  Token Exchange) into a `502` carrying PingOne's actual
  `error`/`error_description`** (`_pingone_error_detail()`) — previously
  any PingOne rejection here crashed as an unhandled exception, surfacing
  to the frontend as an opaque `500 Internal Server Error: Internal Server
  Error` with zero diagnostic value. If you see that exact bare-500 shape
  from a different endpoint, the fix pattern (catch `httpx.HTTPStatusError`
  around the call, raise `HTTPException` with the response body) is the one
  to copy — don't let an upstream OAuth rejection surface as a raw crash.
- **`authlib.jose` is deprecated** in favor of `joserfc` (same author) as of
  authlib 1.7+ — this codebase uses `joserfc` throughout for JWT/JWE, don't
  add `authlib` back for jose functionality.
- **Don't use the `gitleaks/gitleaks-action` marketplace Action** — v2+
  requires a paid license for org-owned repos, and GitHub removes Node 20
  from hosted runners on 2026-09-16, which breaks it outright regardless of
  license. `.github/workflows/gitleaks.yml` installs and runs the raw OSS
  `gitleaks` binary directly instead (no license, no Node dependency) —
  keep it that way rather than "simplifying" to the marketplace Action.
- Don't broadly `pkill -f vite` / `pkill -f uvicorn` when testing — it kills
  every matching process, including ones the user started themselves. Same
  caution now applies to `pkill -f "uvicorn app.main:app"` — matches
  `backend/`, `task-agent/`, *and* `mcp-todos-server/`; kill by port (8000 /
  9010 / 9000) or PID instead. Same for `pkill -f vite` — matches both
  `frontend/` (5173) and `mcp-todos-server/frontend/` (5174).
- **`backend/.env`, `task-agent/.env`, and `mcp-todos-server/.env`'s
  `OIDC_DISCOVERY_URL` / `AGENT_EXPECTED_AUDIENCE` must match exactly** —
  the Task Agent and `mcp-todos-server` both independently verify the same
  delegated token, each with its own copy of the same config. A mismatch
  here fails the same way a real misconfigured PingOne resource would
  (`audience_mismatch` / `issuer_mismatch`), which is correct behavior, not
  a bug — but it's easy to forget to update all three.
- **Running `python script.py` (not `-c`) with `uv run` doesn't put the
  service's own directory on `sys.path`** the way `-c`/inline code does —
  Python adds the *script's* directory, not cwd. A standalone test script
  living outside `backend/`/`task-agent/` needs `PYTHONPATH=.` (or to live
  inside the service directory) to import `app.*`.
- **`langgraph.prebuilt.ToolNode`'s `awrap_tool_call` hook** is the right
  place for any per-tool-call cross-cutting check (policy ACLs, rate
  limits, etc.) — `request.runtime.config["configurable"]` is how it reads
  values threaded in from the outer `graph.ainvoke(...)`/`astream_events`
  call. This is what `task-agent/app/graph.py`'s policy gate uses; prefer
  it over wrapping individual `@tool` functions by hand.
- A2A client responses are protobuf (`a2a_pb2.StreamResponse` from
  `client.send_message(...)`) — check `chunk.HasField("task")`, then
  `chunk.task.artifacts[-1].parts[-1].text` for the final answer, not a
  plain Python object with dict-like access.
- **`AIMessage.content` is `str | list[dict]`, not always a plain string** —
  a multi-block response shape crashed `a2a.helpers.new_text_part(text=...)`
  (which needs a real `str`) inside `task-agent/app/agent_executor.py` once
  a completion response happened to come back as blocks. Both
  `backend/app/routes/invoke.py` and `task-agent/app/agent_executor.py` now
  have their own `_extract_text()` normalizing this — reuse that shape
  anywhere else `AIMessage.content` gets read directly.
- **`backend/.env`, `task-agent/.env`, and `mcp-todos-server/.env`'s
  `TODOS_READ_SCOPE` / `TODOS_WRITE_SCOPE` must also match exactly**, same
  reasoning as `AGENT_EXPECTED_AUDIENCE` above — `task-agent/app/policy.py`
  and `mcp-todos-server/app/policy.py` each check the verified token's
  `scope` claim against their own copies of these two values.
- **`fastmcp`'s `mcp.http_app(path=...).lifespan` must be forwarded into
  the parent FastAPI app's own `lifespan`** when mounting an MCP sub-app
  inside a bigger FastAPI app (`mcp-todos-server/app/main.py`) — otherwise
  the streamable-http session manager never starts and every MCP call
  hangs/fails. `get_http_request()` (from `fastmcp.server.dependencies`)
  is how an `@mcp.tool` function reads the raw `Authorization` header
  without needing an explicit `ctx: Context` parameter.

## Skills

- `run` — start all services correctly (handles the cwd/Node gotchas above;
  now covers `backend/`, `frontend/`, `task-agent/`, `mcp-todos-server/`,
  and `mcp-todos-server/frontend/`).
- `extend-agent-graph` — read before adding any LangGraph node, tool, or
  agent; has the full worked pattern (inbound auth + policy gate shapes) to
  copy for anything new.
