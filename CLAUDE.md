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

**Chained RFC 8693 delegation, not one pass-through token (redesigned
2026-08-16 — see "RFC 8693 chained delegation" section below for the full
picture).** There is no single "the agent is authenticated" state, and as
of the 2026-08-16 redesign there is also no single token forwarded
unchanged hop to hop. Each service that needs to act mints its own
delegation credential, scoped to exactly what *it* is about to do, right
before it does it:

- The Chat Agent never requests `todos:read`/`todos:write` at all anymore
  — it holds exactly one generic delegation credential per session
  (scope `agent:delegation`, addressed to the Task Agent), obtained via
  Client Credentials (proving its own identity) + one RFC 8693 Token
  Exchange (`POST /api/auth/agent-token`, no request body — approving is a
  single, session-wide action now, not per todos-scope).
  `ask_task_agent_read`/`ask_task_agent_write` (`backend/app/agent/tools.py`)
  still exist as two separate tools — the model's tool *choice* still
  shapes how it phrases the request text sent onward — but both forward
  the identical delegation token; neither selects a different OAuth scope
  at this layer anymore.
- The Task Agent is what actually decides `todos:read` vs `todos:write`,
  and it does so **per tool call, freshly, every time** — this is where
  "step-up" scoping now lives, not at the Chat Agent. See `task-agent/app/graph.py`'s
  `_scoped_tool_call`.
- When the Chat Agent has no delegation credential yet, `_delegate()`
  returns a sentinel (`NEEDS_AGENT_AUTH_MARKER`); `routes/invoke.py` turns
  it into a generic `auth_required` SSE event (no scope payload anymore).
  The frontend renders `InlineAgentApprovalPrompt.tsx` — one generic
  "Approve Agent Action" prompt, not a per-scope one (`SCOPE_LABELS` and
  the whole per-scope copy are gone).

**The scoping is enforced, not just labeled in the UI** — this is the part
that would be easy to skip and shouldn't be. `task-agent/app/policy.py`'s
`check()` is now identity-ACL-only (the inbound token's scope is always
the generic `agent:delegation`, so comparing it against `todos:read`/
`todos:write` would be meaningless) — the *real* enforcement of which
specific action is allowed is PingOne itself, via whether the Task Agent's
own per-tool-call Token Exchange for that exact scope succeeds, and
`mcp-todos-server/app/policy.py`'s independent check on the resulting
token (which *does* still check scope, since that token genuinely carries
it). I verified this holds even adversarially, both before and after the
redesign: a validly-signed, correctly-audienced token scoped *only*
`todos:read`, presented directly to whichever service is the actual
enforcement point, asking it to write, was independently rejected —
confirmed no mutation occurred.

None of this weakens the AgentCore-inbound-auth story — every hop still
hard-rejects any call without a correctly-scoped, freshly-verified
delegated token, independent of whatever the previous hop believed; there
are just more hops now, each doing real work instead of one hop forwarding
a single token unchanged.

**Rule for any future work**: if you're adding an authorization/policy
check, it is a plain function/FastAPI dependency, not a graph node — even if
it gets a persona-like name ("policy agent") in telemetry or UI copy for
storytelling purposes. `task-agent/app/policy.py`'s `check()`, called from
`TaskAgentExecutor.execute()` (via `ToolNode`'s `awrap_tool_call` hook,
which now also drives the actual scoped MCP call — see
`_scoped_tool_call`) before the MCP server is ever touched, is the worked
example — copy that shape, don't reinvent it.

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
- `task-agent/app/telemetry.py` is a second, independently-duplicated copy
  of this same shape (own ring buffer, own `/telemetry` endpoint) — see
  "Telemetry for the Task Agent" below. Every span dict from either copy
  now carries a `service` field so the frontend can tell the two apart
  when a span *name* recurs across services (e.g. `inbound_auth.verify`).
  If you add a third instrumented service, give it the same `service`
  stamp — don't assume span names are globally unique.

## LangGraph + real A2A: two graphs, two processes (built 2026-08-15)

Both graphs use the standard LangGraph ReAct loop shape (prebuilt
`ToolNode` + `tools_condition`): an assistant node reasons and optionally
emits a tool call, a tools node executes it, loop back to the assistant to
turn the result into a natural-language answer, then `END`.

**Chat Agent graph** (`backend/app/agent/graph.py`) — 2 nodes:
- `assistant` — Claude, with `ask_task_agent_read` and `ask_task_agent_write`
  (`backend/app/agent/tools.py`) bound via `bind_tools()`. Decides itself,
  via real tool-calling, whether to delegate — the model's tool *choice*
  still shapes the natural-language request text sent to the Task Agent,
  but (since the 2026-08-16 redesign) no longer selects a different OAuth
  scope; both tools forward the same single delegation credential.
- `tools` — a `ToolNode` wrapping both, which are *not* LLM calls: each is a
  real A2A client call (`a2a.client.create_client`) to the Task Agent, over
  HTTP/JSON-RPC, not an in-process function call. Forwards
  `config["configurable"]["delegated_token"]` (a single token, not a dict
  keyed by scope anymore) — set in `backend/app/routes/invoke.py`.

**Task/Specialist Agent graph** (`task-agent/app/graph.py`, separate
process, own A2A server via `a2a-sdk`) — 2 nodes, but the real work moved
into the tool-execution hook (see "RFC 8693 chained delegation" below):
- `task_assistant` — its own Claude call, reasons about which MCP tool to
  invoke (`list_todos`/`add_todo`/`complete_todo`). Tools are fetched from
  `mcp-todos-server/` via `langchain_mcp_adapters.MultiServerMCPClient`
  once per task **purely for their schemas** (`tools/list` needs no auth)
  — bound to the LLM the same way as the Chat Agent's tools, but never
  actually invoked through that connection.
- `execute_tool` — a `ToolNode` whose `awrap_tool_call` hook
  (`_scoped_tool_call`) does full custom dispatch, not just a gate: policy
  check, then this service's own delegation chain (Client Credentials +
  a **fresh, per-call** RFC 8693 Token Exchange, scoped to exactly the
  todos capability the specific tool needs), then a freshly-authorized MCP
  connection built just for that one real call. Denied/failed calls return
  a `ToolMessage` explaining why, so the model can tell the user, rather
  than silently failing.

`TaskAgentExecutor.execute()` (`task-agent/app/agent_executor.py`) runs
inbound auth *before* invoking this graph at all — pulls the bearer token
out of `context.call_context.state["headers"]`, verifies it (audience =
this service's own URL, scope must contain `agent:delegation`), and only
then calls `graph.ainvoke(...)`, threading `identity.agent_client_id`
(**not** `identity.client_id`, see below) as `configurable["client_id"]`
for the policy check, and the raw verified token itself as
`configurable["delegation_token"]` — that's what `_scoped_tool_call` later
uses as the *subject* token for its own exchange.

## RFC 8693 chained delegation: four exchanges, six PingOne apps (redesigned 2026-08-16)

**What changed and why.** The original design (2026-08-15, see git history)
had the Chat Agent perform the *only* RFC 8693 exchange — scoped directly
to `todos:read`/`todos:write` — and forward that one token unchanged
through the Task Agent to `mcp-todos-server`. That's a reasonable MVP, but
it isn't how a real deployment would want authority to flow: the entity
that should decide *and request* the specific downstream capability is the
one that's actually about to use it (the Task Agent), not an upstream
orchestrator minting a todos-scoped token it never itself touches a todo
with. The redesign makes each hop mint its own credential, scoped to
exactly what *that hop* needs, right before it acts — closing the
CLAUDE.md-documented gap ("task-agent having its own distinct PingOne
identity") along the way.

**The full chain**, in order:

1. **Chat Agent proves its own identity** — Client Credentials,
   `AGENT_CLIENT_ID`/`AGENT_CLIENT_SECRET` (PingOne app #2), scope
   `agent_own_scope` (`agent:orchestration`). Resulting actor token's
   `aud` resolves to the Chat Agent's own URL (`http://localhost:8000`) —
   determined by whichever PingOne resource owns that scope, same "audience
   comes from the scope's resource" pattern used everywhere in this app.
2. **Chat Agent delegates to the Task Agent** — RFC 8693 Token Exchange,
   using a **different** PingOne app (#3, `AGENT_DELEGATION_CLIENT_ID`/
   `AGENT_DELEGATION_CLIENT_SECRET` — deliberately not the same app as step
   1: "an agent proving who it is" and "a client privileged enough to mint
   delegated tokens" are different trust levels in a real deployment).
   `subject_token` = the user's own PingOne session access token,
   `actor_token` = step 1's actor token, scope always
   `agent_delegation_scope` (`agent:delegation`) — generic, never
   action-specific. Resulting token's `aud` resolves to the Task Agent's
   URL (`http://localhost:9010`). This is the **only** credential the Chat
   Agent holds; `backend/app/auth/routes.py`'s `/api/auth/agent-token`
   takes no request body anymore and caches exactly one token
   (`EXCHANGED_TOKEN_COOKIE`, no longer a dict keyed by scope).
3. **Task Agent proves its own identity** — Client Credentials, a THIRD
   distinct PingOne app (#5, `TASK_AGENT_CLIENT_ID`/
   `TASK_AGENT_CLIENT_SECRET`), scope `agent_task_scope` (`agent:task`).
   `aud` resolves to the Task Agent's own URL — same self-referential
   pattern as step 1, one hop later. Done once per task and reused across
   however many tool calls that task makes (`task-agent/app/graph.py`'s
   `actor_cache`) — it doesn't vary by which tool is being called.
4. **Task Agent requests the actual MCP-scoped capability** — RFC 8693
   Token Exchange, a FOURTH distinct PingOne app (#6, `TODOS_MCP_CLIENT_ID`/
   `TODOS_MCP_CLIENT_SECRET`). `subject_token` = the token received in
   step 2 (**not** the raw human token — the Task Agent never sees that
   directly; RFC 8693 exchange chains preserve the original subject's
   `sub` through each hop, confirmed via real tokens below, which is what
   keeps OBO attribution intact all the way to `mcp-todos-server`'s audit
   log), `actor_token` = step 3's token, scope = `todos_read_scope` **or**
   `todos_write_scope` — whichever the specific tool about to be called
   needs, decided fresh, **every single tool call**
   (`task-agent/app/graph.py`'s `_scoped_tool_call` /
   `_get_mcp_scoped_token`). `aud` resolves to `mcp-todos-server` itself
   (`http://localhost:9000/mcp`).

**This is what gives step-up scoping for free, with zero extra state.**
Nothing about *which* scope was granted is cached across calls — only the
Task Agent's own step-3 actor token is (since it never varies). A `todos:read`
exchange succeeding for `list_todos` has no bearing on whether a later
`todos:write` exchange succeeds for `add_todo` in the same conversation;
each is requested fresh, independently, exactly when needed. If PingOne
rejects a specific scope request (the underlying authorization doesn't
actually cover it), `_scoped_tool_call` returns a clear `ToolMessage`
explaining that, which the model relays — there's no separate interactive
"click to grant write" UI loop built for this yet (see "Not yet built").

**Critical claim discovery, easy to get backwards: `agent_client_id` vs
`client_id`.** Confirmed via real PingOne tokens (not documentation) on
2026-08-16: an RFC 8693 exchange response's top-level `client_id` claim is
just whichever app *authenticated that specific exchange call* (step 2's
token has `client_id` = the app #3 that performed *that* exchange, not
app #2 from step 1). The claim that actually means "which agent is
delegating" is a **custom claim PingOne populates from the actor token**,
`agent_client_id` — step 2's token carries `agent_client_id` = app #2's
own client_id (the Chat Agent's self-proven identity from step 1); step
4's token carries `agent_client_id` = app #5's client_id (the Task Agent's
self-proven identity from step 3). Every policy ACL in this codebase
(`task-agent/app/policy.py`, `mcp-todos-server/app/policy.py`) must check
`VerifiedIdentity.agent_client_id`, **not** `.client_id` — the two were
briefly confused in both places before this was caught. `shared/inbound_auth.VerifiedIdentity`
carries both fields; `client_id` is documented inline as "a mechanical
detail, not necessarily the agent."

**`task-agent/app/policy.py` no longer checks scope, only identity.** The
inbound token's scope is always the generic `agent:delegation` regardless
of intended action, so a local comparison against `todos:read`/
`todos:write` would always fail (or be meaningless if it happened to
match by accident). Real enforcement of *which* action is allowed is now:
does step 4's exchange for that specific scope succeed, and does
`mcp-todos-server/app/policy.py`'s independent check pass (that one *does*
still compare scope — the token it receives genuinely carries
`todos:read` and/or `todos:write`, since that's the whole point of step 4).

**Six PingOne apps total now** (see README "PingOne setup" for the exact
grant types / redirect URIs / resource config for each):
1. User sign-in (OIDC Web App) — unchanged from the original design.
2. Chat Agent's own identity (`AGENT_CLIENT_ID`) — Client Credentials only.
3. Chat Agent's delegation-exchange app (`AGENT_DELEGATION_CLIENT_ID`) —
   Client Credentials + Token Exchange.
4. `mcp-todos-server`'s own sign-in (OIDC Web App) — unchanged.
5. Task Agent's own identity (`TASK_AGENT_CLIENT_ID`) — Client Credentials only.
6. Task Agent's MCP-exchange app (`TODOS_MCP_CLIENT_ID`) — Token Exchange.

**New module**: `task-agent/app/token_grants.py` — `client_credentials_grant()`/
`token_exchange()`/`get_token_endpoint()`, deliberately duplicated from
`backend/app/auth/agent_auth.py`'s identical shape rather than shared
(same reasoning as everywhere else: `shared/` is for inbound-auth
verification only, each service's own outbound grant calls stay
separately auditable).

### Verified end-to-end (2026-08-16)

Real PingOne tokens throughout — every claim shape mentioned above was
confirmed by actually running the grant/exchange calls against the live
PingOne tenant and inspecting the resulting JWTs, not assumed from RFC
text. Two chained-exchange tokens inspected directly:

- Step 2's token: `aud: ["http://localhost:9010"]`, `scope: "agent:delegation"`,
  `sub` = the human's own PingOne sub, `agent_client_id` = app #2's
  client_id. `identity.agent_client_id` matched `task-agent/.env`'s
  existing `ALLOWED_AGENT_CLIENT_ID` with no changes needed there —
  confirming the ACL was already (accidentally) pointed at the right value
  even before the claim-source bug was caught.
- Step 4's token: `aud: ["http://localhost:9000/mcp"]`,
  `scope: "todos:read todos:write"`, `sub` **unchanged** from step 2's
  token (and from the original human session token — OBO subject
  preserved through two chained exchanges), `agent_client_id` = app #5's
  client_id (Task Agent's own identity), plus a real `email` claim
  (`robertluisi@pingidentity.com`) that step 2's token didn't carry —
  meaning `mcp-todos-server`'s audit log can resolve the human's label
  directly from this token for calls that reach it, no session-cache
  fallback needed.
- The full live A2A round-trip (real running processes, not isolated
  function calls) was also exercised for the earlier failure checkpoints
  in this redesign: an `agent:delegation`-scoped token forwarded to a Task
  Agent that didn't yet understand it correctly failed with a graceful
  `TASK_STATE_FAILED` + `audience_mismatch` explanation — proving the
  A2A/inbound-auth plumbing surfaces a real upstream rejection cleanly
  rather than crashing, before the rest of the chain was wired up.

### Not yet built

- Cross-service telemetry for `mcp-todos-server/` and the MCP-scoped
  token-exchange internals (steps 3/4 of the RFC 8693 chain) — those still
  only show up via task-agent's own console/logs. `task-agent/` itself
  *does* now have a `RecordingSpanProcessor`/`/telemetry` endpoint (see
  "Telemetry for the Task Agent" below, added 2026-08-16) — its A2A task
  execution, inbound auth, and judge verdicts are live in the frontend's
  Telemetry panel and diagram, same as the Chat Agent's.
- An interactive step-up consent loop: if the Task Agent's own
  `todos:write` exchange fails because the underlying authorization
  genuinely doesn't cover it, the model explains this in prose, but
  there's no dedicated UI signal (an `auth_required`-style SSE event, an
  inline prompt) distinguishing "needs a broader grant" from any other
  tool failure yet — unlike the Chat-Agent-level `NEEDS_AGENT_AUTH_MARKER`
  flow, which is fully interactive.

## Judge node in the Task Agent's graph (added 2026-08-16)

`task-agent/app/graph.py` gained a third node, `judge` — after
`task_assistant` produces what would otherwise be the final answer, an
LLM call (`ChatAnthropic(...).with_structured_output(JudgeVerdict)`,
`JudgeVerdict` a Pydantic model with `status: Literal["pass","fail"]`,
`reason: str`, `missing_info: list[str]`) evaluates it against
`AgentState["delegated_request"]` and decides whether to let it through or
loop back to `task_assistant` with feedback. Toggle: `Settings.judge_enabled`
(default `True`); disabling it restores the exact pre-judge graph shape
(`task_assistant`'s `tools_condition` wired straight to `END`).

**This is correctly a graph node, unlike every auth/policy check in this
codebase.** CLAUDE.md's core rule ("auth never inside the graph") is
specifically about *authorization* decisions the model's own output
shouldn't be able to influence. A judge's entire job is being the model's
own quality judgment — a different category — so it belongs inside the
graph. It also needs no identity of its own: it only evaluates text
already produced, never touches a protected resource, so it never crosses
the delegation boundary the rest of this app's auth chain exists to gate.

**What "the request" means here was a real design question, resolved
mid-planning.** Task Agent never sees the human's literal chat message —
`backend/app/agent/tools.py`'s `ask_task_agent_read`/`write` take a
`request: str` the *Chat Agent's own LLM* writes when it decides to
delegate, and that string (captured via `get_message_text(context.message)`
in `task-agent/app/agent_executor.py`) is what the judge compares against,
stored as `AgentState["delegated_request"]` — deliberately not named
`original_request`, which would overstate what it is. This is the
architecturally correct scope, not just the cheap one: Task Agent's actual
contract is with its caller (the Chat Agent), not an end human it never
talks to directly; if the Chat Agent's own paraphrase drops something the
user asked for, that's a Chat-Agent-level fidelity concern, outside what a
Task-Agent-side judge can or should catch.

**Fails open, not closed — the opposite direction from every security
check in this app.** A broken judge call (exception, malformed structured
output) is caught and treated as `"pass"`; a quality-control mechanism
malfunctioning should never block a real, working answer from reaching the
user, unlike an auth check, which must always fail closed.

**Bounded retries**: `Settings.judge_max_attempts` (default `2` — the
original attempt plus one retry). Exceeding it sets
`judge_status = "gave_up"` and returns the last answer produced rather
than looping forever or hard-failing the task. Verified live
(`judge_enabled=true`, no delegation token so every tool call was denied):
attempt 1 failed the judge (agent gave up without genuinely trying),
attempt 2 also failed, attempt 3 short-circuited straight to `"gave_up"`
without even calling the LLM — exactly `judge_max_attempts` semantics.
`judge_enabled=false` reproduces the original single-pass behavior
byte-for-byte (empty `judge_status`, first answer returned unchanged).

**Demo visibility is `logging`, not OpenTelemetry** — task-agent has no
`RecordingSpanProcessor`/telemetry endpoint at all yet (see "Not yet
built" above), so the judge logs each verdict (`logger.info` — attempt
number, status, reason) to its own console rather than emitting a real
span. Wiring this into real spans is a natural follow-up once cross-service
telemetry aggregation exists, not part of this change.

**Small bundled cleanup**: `_extract_text()` (the `AIMessage.content:
str | list[dict]` normalizer) had separate copies in
`task-agent/app/agent_executor.py` and `backend/app/routes/invoke.py`. The
judge needed the same normalization to read the proposed answer, so the
canonical copy for this service moved into `task-agent/app/graph.py`
(`agent_executor.py` now imports it from there) — `backend/`'s stays
separate, different service, not shared.

**The judge can run on a different LLM provider than `task_assistant`**
(`Settings.judge_provider`, `"anthropic"` default or `"groq"`) — deliberate,
because the judge is a pure text-in/structured-verdict-out evaluation with
no tool calls, no delegation token, and no identity of its own, unlike
`task_assistant`'s LLM. `task-agent/app/graph.py`'s `_build_judge_llm()`
constructs either `ChatAnthropic` or `ChatGroq` and returns a plain
`BaseChatModel`; `_build_judge_node()` calls `.with_structured_output(JudgeVerdict)`
on whichever came back, unchanged. Groq's free tier is genuinely free
(rate-limited, not credit-metered) and its hosted Llama models support
tool-calling, which `.with_structured_output()` needs under the hood — the
default Groq judge model is `llama-3.3-70b-versatile`
(`_DEFAULT_GROQ_JUDGE_MODEL`), used when `JUDGE_MODEL` is unset. Set via
`task-agent/.env`: `JUDGE_PROVIDER=groq` + `GROQ_API_KEY=<free key from
console.groq.com>`. If `GROQ_API_KEY` is empty or invalid while
`JUDGE_PROVIDER=groq`, the judge call fails and the fail-open behavior
above kicks in (`status="pass"`) — the task still returns an answer, just
without a real judge pass, rather than crashing the task.

## Telemetry for the Task Agent (added 2026-08-16)

`task-agent/app/telemetry.py` is a copy of `backend/app/telemetry.py`'s
shape (`RecordingSpanProcessor` ring buffer, the same `token|secret|
password|authoriz` redaction filter, `with_span()`), deliberately
duplicated rather than shared — same reasoning as `token_grants.py`: each
service's telemetry stays independently auditable. `app/main.py` calls
`init_telemetry()` in its lifespan and exposes `GET /telemetry` (no `/api`
prefix — task-agent's other routes are the A2A JSON-RPC endpoint at
exactly `/`, a distinct path, so there's no route-mount collision to
worry about the way `mcp-todos-server/app/main.py` has to for `/mcp`).

**`a2a-sdk` auto-instruments itself onto any registered `TracerProvider`
— must be explicitly disabled, or the panel fills with EventQueue noise.**
`a2a/utils/telemetry.py`'s `@trace_class`/`@trace_function` decorators
(applied to `EventQueue`, `DefaultRequestHandler`, both the JSON-RPC
server dispatcher *and* the A2A client transport backend/ uses to call
this service) read `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED` **once, at
import time** and default to *enabled* whenever any real `TracerProvider`
is registered — before this app called `init_telemetry()`, that
auto-instrumentation was silently inert (no-op tracer). Both
`task-agent/app/main.py` and `backend/app/main.py` now set
`os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")`
as the literal first lines of the file, before the `a2a.*` imports below
them — it has to run before a2a's own `telemetry` submodule is imported
for the first time anywhere in the process (including transitively, e.g.
via `app.agent.graph` → `app.agent.tools` on the backend side), or the
env var check has already happened and setting it later does nothing.
This is unrelated to our own spans (`inbound_auth.verify`,
`a2a.task_execute`, `judge.evaluate`, `agent.invoke`,
`agent.a2a_delegate`, …) — those use this app's own `with_span()`/tracer,
not a2a-sdk's, and are unaffected either way.

**Three spans, one nested under another**: `inbound_auth.verify`
(`agent_executor.py`, its own top-level span — same "the gate happens
before the graph runs" reasoning as the Chat Agent's) and `a2a.task_execute`
(also `agent_executor.py`, wraps the whole graph invocation) are siblings;
`judge.evaluate` (`graph.py`, one instance per judge attempt) nests under
`a2a.task_execute` via OTel's ambient context, so a trace shows the full
propose → evaluate → retry loop as one tree, not three unrelated spans.

**`judge.evaluate`'s attributes are deliberately ordered `judge.attempt`
then `judge.status` first**, before `judge.reason`/`judge.provider`/
`judge.missing_info_count` — `StoryNode.tsx` (frontend diagram) only
previews a matched span's first 3 attributes, and pass/fail is the one
thing this instrumentation exists to surface there. `judge.status="fail"`
is a normal business outcome, not a span execution failure, so the span's
OTel status stays `OK` even on a fail verdict — a red/`ERROR` span in this
panel means the judge *call itself* broke (the fail-open except branch),
never that it did its job and said no.

**Cross-service span-name collisions, solved via a `service` field, not
by renaming spans.** Both this service and the Chat Agent emit a span
literally named `inbound_auth.verify` (same phase, different process) —
once the frontend merges both services' spans into one array (see below),
matching by name alone would attribute either service's auth check to
either node. `RecordingSpanProcessor.on_end()` (both copies) now stamps
every emitted span dict with `"service": span.resource.attributes.get("service.name")`
— `"agentorchestration-console-backend"` or
`"agentorchestration-console-task-agent"` — and
`frontend/src/components/diagram/flowConfig.ts` exports these as
`SERVICE_CHAT_AGENT`/`SERVICE_TASK_AGENT`. `latestSpan(spans, names, service?)`
filters on both; every `StoryNodeData`/`StoryEdgeMeta` entry with a
`spanNames`/`spanName` now also carries the matching `service`. Any
future service that reuses a span name (e.g. `mcp-todos-server` adding
its own `inbound_auth.verify` per the shared `verify_bearer_token()`
pattern) must do the same.

**Frontend fetches task-agent's spans directly, not via the Chat Agent.**
`frontend/vite.config.ts` proxies `/task-agent-api/*` → `http://localhost:9010/*`
(same "same-origin in dev" reasoning as the existing `/api` → backend
proxy — no CORS middleware needed on task-agent). `TelemetryPanel.tsx`
polls both `api.getTelemetry()` (backend) and `api.getTaskAgentTelemetry()`
(task-agent) every tick via `Promise.allSettled`, so task-agent being down
degrades to "just the Chat Agent's spans," not a broken panel. The merged
array is sorted by `start_time` — the two ring buffers are each only
locally chronological, so concatenating without sorting would interleave
them wrong.

**Bug fixed in passing: `latestSpan()`'s input ordering.** Before this
change, `TelemetryPanel` reversed the API's spans to newest-first for its
own card list, then passed that *same reversed array* into
`ArchitectureDiagram` — but `latestSpan()` scans from the end assuming
newest-*last* input (matching the raw, un-reversed API order its docstring
describes). With a single service and mostly-unique span names per demo
session this rarely surfaced, but it meant "most recent matching span"
was actually returning the *oldest* match once a span name recurred (e.g.
a second `inbound_auth.verify` later in the same session) — the edge/node
would key off a stale match and could stop updating. Fixed by keeping the
`spans` state itself in raw (oldest-first) order and reversing only at
render time for the panel's own list (`spans.slice().reverse().map(...)`).

**The `e-verify-task` edge (Task Agent → PingOne, "independently
re-verifies the SAME token") is now genuinely live** — it previously had
`meta: {}` (nothing instrumented) since task-agent had no spans at all.
Same for the new `judge`/`e-judge-propose`/`e-judge-retry` diagram
elements added alongside the evaluator-optimizer pattern (see "Judge node
in the Task Agent's graph" above) — those now key off the real
`judge.evaluate` span instead of always rendering dashed/"not aggregated
here."

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

**mcp-todos-server now receives a token task-agent minted itself, not one
forwarded unchanged.** Superseded by the 2026-08-16 redesign above:
`task-agent/app/graph.py`'s MCP tools are fetched once per task purely for
schemas (no auth needed for `tools/list`); the actual, freshly-authorized
connection is built per tool call inside `_scoped_tool_call`, using a
token from task-agent's *own* RFC 8693 exchange (step 4 in the chain
above) — not a header carried over from the Chat Agent. `mcp-todos-server`
still independently re-verifies whatever it receives, same as always;
what changed is *whose* exchange produced it.

**`mcp-todos-server` gates independently of `task-agent`, not trusting its
gate.** `mcp-todos-server/app/policy.py` mirrors `task-agent/app/policy.py`'s
identity-ACL shape, but *keeps* the scope check task-agent's own copy
dropped (see "RFC 8693 chained delegation" above for why the two now
differ) — the token this service receives genuinely carries the specific
`todos:read`/`todos:write` scope, so comparing it is still meaningful
here. This service doesn't assume task-agent already enforced anything,
the same "adversarial" posture task-agent itself takes toward the Chat
Agent. A denied call still gets audited (`outcome="denied"`), not silently
dropped. Like task-agent's, this ACL checks `caller.agent_client_id`, not
`caller.client_id` — see "RFC 8693 chained delegation" above; both
`mcp_server.py` and `policy.py` had this backwards briefly before real
tokens caught it.

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

**The "must match exactly" env-var story changed shape in the 2026-08-16
redesign — audiences are now deliberately *different* per hop, not
shared.** Before the redesign, a single `AGENT_EXPECTED_AUDIENCE` value had
to match across `backend/.env`, `task-agent/.env`, and
`mcp-todos-server/.env` because the same token was forwarded unchanged
through all three. That's no longer true: each service now expects the
audience *it itself* is addressed as (`backend`'s own URL for its
inbound-invoke check, `task-agent`'s own URL for the delegation token it
receives, `mcp-todos-server`'s own URL for the token task-agent's own
exchange produces) — see the six-app table above. What still must match
across services: `OIDC_DISCOVERY_URL` (same PingOne tenant everywhere),
and `TODOS_READ_SCOPE`/`TODOS_WRITE_SCOPE` between `task-agent/.env` and
`mcp-todos-server/.env` specifically (not `backend/.env`, which no longer
has these settings at all).

## What's built (as of 2026-08-15)

- Sign in with PingOne (OIDC Authorization Code + PKCE S256, JWE-encrypted
  session cookie) — sufficient on its own for plain chat.
- Chained RFC 8693 delegation across four exchanges and six PingOne apps
  (Client Credentials + Token Exchange at both the Chat Agent and the Task
  Agent) — required only for the agent to act on the user's behalf, each
  hop scoped to exactly what it needs, enforced fresh via inbound auth at
  every hop, not at the chat gate. See "RFC 8693 chained delegation" above.
- Inline in-chat approval: asking for something that needs delegation gets
  a graceful explanation plus a single, generic `InlineAgentApprovalPrompt`
  ("Approve Agent Action" — one action per session now, not per todos
  scope), which auto-retries on success. There's no header-level
  "Authenticate Agent" button — approval is purely contextual.
- Step-up scoping at the Task Agent: which todos capability (read vs.
  write) is requested is decided fresh per tool call, with nothing cached
  across different scopes — see "RFC 8693 chained delegation" above.
- OpenTelemetry spans + redaction + ring buffer, live in the Telemetry
  panel — plus a "Diagram" button (`TelemetryPanel.tsx`) opening a
  React Flow (`@xyflow/react`) architecture diagram
  (`frontend/src/components/diagram/`) that animates real span data onto
  the actual identity/data-flow graph for demo narration; code-split via
  `React.lazy` so it costs nothing until opened.
- LangGraph chat agent (Claude), streamed over SSE, Markdown-rendered,
  per-thread checkpointed — 2 nodes, with real, chained-delegation A2A
  access to a separate Task Agent service for todos (see "LangGraph + real
  A2A" and "RFC 8693 chained delegation" above).
- `task-agent/` — own A2A server, own distinct PingOne identity, own
  step-up-scoped exchange to `mcp-todos-server/` — in-memory, no
  persistence, but no longer unauthenticated: its own PingOne-gated UI
  (todos tagged human vs. agent), its own policy gate, and an OBO audit
  log. See "MCP server: its own UI, PingOne SSO, and an OBO audit log" above.
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

- **`a2a-sdk` self-instruments onto any registered OTel `TracerProvider`**
  — `EventQueue`/request-handler/client-transport methods start emitting
  spans the moment `init_telemetry()` registers a real provider, flooding
  the Telemetry panel with plumbing noise unrelated to this app's own
  spans. Fixed via `os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED",
  "false")` as the literal first lines of both `backend/app/main.py` and
  `task-agent/app/main.py` — must run before the first `a2a.*` import
  anywhere in the process, since a2a-sdk reads the env var once at that
  submodule's import time. See "Telemetry for the Task Agent" above.
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
- **Every PingOne resource behind any scope in this app must issue JWT
  access tokens**, not opaque/reference tokens — inbound auth can only
  verify a JWT cryptographically. There are four exchanges now (see "RFC
  8693 chained delegation"), each against a potentially different
  resource — this bites per-resource, not once globally. If a verification
  401s with `audience_mismatch`, the error message includes the actual
  `aud` value PingOne issued; that's also how you discover which resource
  a given scope actually belongs to, since **the audience is never passed
  as an explicit request parameter anywhere in this codebase — it's
  entirely a consequence of which PingOne resource owns the scope you
  requested.** Getting a token with the wrong `aud` almost always means
  the scope was defined on the wrong resource in PingOne's console, not a
  code bug.
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
- **`OIDC_DISCOVERY_URL` must match across all four services** (same
  PingOne tenant everywhere) — **but `AGENT_EXPECTED_AUDIENCE` must NOT
  match across services anymore**, unlike before the 2026-08-16 redesign.
  Each service now expects the audience *it itself* is addressed as:
  `backend/.env`'s is its own URL (`http://localhost:8000`),
  `task-agent/.env`'s is its own URL (`http://localhost:9010`),
  `mcp-todos-server/.env`'s is its own URL
  (`http://localhost:9000/mcp`). If you find yourself trying to make these
  three the same value, that's the old design — see "RFC 8693 chained
  delegation" above.
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
- **`task-agent/.env` and `mcp-todos-server/.env`'s `TODOS_READ_SCOPE` /
  `TODOS_WRITE_SCOPE` must match exactly** — `backend/.env` no longer has
  these settings at all (the Chat Agent doesn't know or care about
  todos:read/write anymore, see "RFC 8693 chained delegation" above);
  it's now a two-service match, not three. `task-agent/app/graph.py`
  requests one of these via its own Token Exchange per tool call, and
  `mcp-todos-server/app/policy.py` checks the resulting token's `scope`
  claim against its own copy.
- **The `agent_client_id` custom claim, not `client_id`, is what every
  policy ACL in this repo checks identity against** — `client_id` on an
  exchanged token is just whichever PingOne app authenticated *that*
  specific exchange call; `agent_client_id` is the claim PingOne
  propagates from the *actor* token, i.e. which agent is actually
  delegating. Confirmed via real tokens 2026-08-16 (see "RFC 8693 chained
  delegation" above) — both `task-agent/app/policy.py` and
  `mcp-todos-server/app/policy.py`/`mcp_server.py` had this backwards
  before real tokens caught it. If a policy ACL denies a call that looks
  like it should be allowed, check which claim is being compared first.
- **`fastmcp`'s `mcp.http_app(path=...).lifespan` must be forwarded into
  the parent FastAPI app's own `lifespan`** when mounting an MCP sub-app
  inside a bigger FastAPI app (`mcp-todos-server/app/main.py`) — otherwise
  the streamable-http session manager never starts and every MCP call
  hangs/fails. `get_http_request()` (from `fastmcp.server.dependencies`)
  is how an `@mcp.tool` function reads the raw `Authorization` header
  without needing an explicit `ctx: Context` parameter.
- **`[project.scripts]` entry points don't get installed when
  `[tool.uv].package = false`** (every service in this repo sets that,
  including `claude-bridge/`) — there's no build/install step to place the
  shim, so `uv run claude-bridge` silently fails to resolve. Launch via
  `uv run --directory claude-bridge python -m app.server` instead
  (confirmed by installing it and finding `.venv/bin/` empty).

## Claude Desktop as the orchestrator (added 2026-08-16)

`claude-bridge/` is a genuinely different way to run this app's chat
experience: instead of the React frontend talking to `backend/`'s
LangGraph `assistant` node, **Claude Desktop's own model becomes the
orchestrator** — deciding when a request needs the Task Agent and
delegating to it — while presenting the *exact same* PingOne identity
`backend/` already has (apps #2/#3). Everything downstream of that
identity (the Task Agent's inbound auth, its own further RFC 8693
exchange, `mcp-todos-server`'s policy ACL and OBO audit log) is completely
unaware anything changed at the front door — this is the whole point:
swap the orchestrator, keep the entire security substrate unchanged and
independently verified exactly as it already is.

**Why a local stdio MCP server, not a remote HTTP connector.** The
original idea was Claude Desktop connecting straight to
`mcp-todos-server`'s `/mcp` endpoint as a remote OAuth-protected MCP
server — rejected mid-design (see the conversation this was built in) for
two reasons: `mcp-todos-server/app/policy.py`'s ACL and
`mcp_server.py`'s audit-log path are both built around the assumption
that every `/mcp` caller went through the RFC 8693 chain and carries an
`agent_client_id` claim, which a token Claude Desktop obtained via a
plain OAuth flow never would; and Claude Desktop's exact remote-connector
OAuth handshake (redirect URI, whether Dynamic Client Registration is
required) wasn't fully pinned down from available docs. A **local stdio
server** sidesteps both: Claude Desktop spawns it as a trusted local
subprocess (no OAuth-with-Desktop-as-client needed at all), and it
presents the Chat Agent's own identity to the rest of the chain — so nothing
downstream needed to change to accommodate a new kind of caller.

**How it authenticates — a self-contained Authorization Code + PKCE flow
with a loopback redirect**, the same pattern CLI tools like `gh auth
login`/`ant auth login` use: `claude-bridge/app/local_login.py` opens a
browser, PingOne redirects to `http://localhost:8765/callback` (an
`http.server.HTTPServer` running in this process, not a real HTTP route
anywhere), and the whole PKCE state (verifier/state/nonce) lives in local
variables for the few seconds the flow takes — no cookie-sealing needed
(`claude-bridge/app/pkce.py` keeps only the stateless PKCE math from
`backend/app/auth/pkce.py`, not the cookie half) since there's no
browser-to-process cookie jar to bridge two separate HTTP requests across,
unlike `backend/`'s `/api/auth/login` + `/api/auth/callback`. This needs
**one PingOne change**: app #1 needs a second redirect URI added
alongside `http://localhost:8000/api/auth/callback`:
`http://localhost:8765/callback` (or whatever `LOCAL_CALLBACK_PORT` is
set to).

**Then the same two-step chain `backend/app/auth/routes.py`'s
`/api/auth/agent-token` performs** — Client Credentials (app #2, "I am
the orchestration agent") + RFC 8693 Token Exchange (app #3, subject =
the human's own token from the browser flow, scope = generic
`agent:delegation`, audience = the Task Agent) — duplicated into
`claude-bridge/app/agent_auth.py` (same shape as
`task-agent/app/token_grants.py`'s duplication, not shared) and
orchestrated with caching by `claude-bridge/app/credentials.py`'s
`CredentialManager`: the delegation credential (cheap, short-lived) is
re-derived often; the underlying browser sign-in (expensive, needs a
human to click through PingOne) is cached until it actually expires. No
disk persistence — a fresh Claude-Desktop-spawned process means a fresh
browser prompt, deliberately: this is a demo bridge, not a credential
store.

**The A2A call itself** (`claude-bridge/app/delegate.py`) is the same
protocol call `backend/app/agent/tools.py`'s `_delegate()` makes — Agent
Cards + task-based JSON-RPC — with the LangChain `@tool`/`RunnableConfig`
wrapper and the `NEEDS_AGENT_AUTH_MARKER` sentinel removed (this bridge
always has a credential by the time it calls the Task Agent; there's no
separate "session vs. delegation" UI moment to signal, the browser prompt
already covered that). Exposes the same two tools
(`ask_task_agent_read`/`ask_task_agent_write`) with the same docstrings,
so Claude Desktop's own tool-choice reasoning shapes the delegated request
text exactly the way the Chat Agent's LangGraph loop already does.

**No OpenTelemetry for this component** — it's a short-lived local
subprocess with no HTTP port for the frontend's Telemetry panel to poll,
so `claude-bridge/app/server.py` narrates via `logging` to stderr instead
(what Claude Desktop's own MCP log viewer shows), same reasoning
task-agent's judge used before it got real spans, except here there's no
long-running server process to eventually add spans to.

**Running it**: add to Claude Desktop's config
(`~/Library/Application Support/Claude/claude_desktop_config.json` on
macOS; `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "task-agent-bridge": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/claude-bridge", "python", "-m", "app.server"]
    }
  }
}
```

Restart Claude Desktop, ask it something todo-related — the first call
pops a browser for PingOne sign-in, everything after that is cached until
it expires.

**A connected server doesn't guarantee Claude reaches for it.** Confirmed
live 2026-08-16: `~/Library/Logs/Claude/mcp-server-task-agent-bridge.log`
showed a fully healthy connection (`initialize`/`tools/list` both
succeeded, both tools returned) with **zero `tools/call` entries** — the
model just never tried. Two separate gates can cause this, and only one
is a triggering problem a skill can fix: (1) Claude Desktop's per-chat
tools toggle being off for this connector (a skill can't fix this — check
the tools icon in the message composer first), and (2) genuine
under-triggering, where the tool is available but the model doesn't map
the phrasing to it. `claude-bridge/skill/SKILL.md` addresses (2) — a
Claude Desktop Agent Skill (same SKILL.md format as this repo's own
`.claude/skills/`) with explicit broad-phrasing trigger guidance, the
real todo data shape (text + done only — **no** due date/priority/tags/
delete, so the model doesn't invent or promise unsupported fields), and a
note on why narrating the delegation is in-character for this demo. Add
it via Claude Desktop's Skills settings, pointed at that folder.

## Skills

- `run` — start all services correctly (handles the cwd/Node gotchas above;
  now covers `backend/`, `frontend/`, `task-agent/`, `mcp-todos-server/`,
  and `mcp-todos-server/frontend/`).
- `extend-agent-graph` — read before adding any LangGraph node, tool, or
  agent; has the full worked pattern (inbound auth + policy gate shapes) to
  copy for anything new.
