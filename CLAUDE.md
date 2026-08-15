# AgentCore Console (LangGraph edition)

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
backend/    FastAPI + LangGraph + OpenTelemetry, Python 3.14 via uv
frontend/   React 19 + Vite 8 (rolldown) + Tailwind v4, Node >=22.12
```

Both were deliberately scaffolded on latest-stable versions, not
conservative defaults — that's a standing preference, not a one-off.

## The core architectural decision: auth is never inside the graph

Every security/authorization check in this app is a deterministic gate that
runs **before** any LangGraph node executes — never as a graph node itself,
and never something an LLM's output influences. This is not a minor style
choice; it's the load-bearing decision behind the whole backend structure:

- `backend/app/auth/inbound.py` (`verify_inbound_token`) re-verifies the
  bearer token *fresh on every call* — signature against PingOne's JWKS,
  issuer, expiry, and **audience** — rather than trusting a session. This
  runs in the FastAPI route handler (`backend/app/routes/invoke.py`)
  *before* `graph.astream_events(...)` is ever called.
- Signing in with PingOne alone is **not sufficient** to chat. The
  "Authenticate Agent" step (Client Credentials → RFC 8693 Token Exchange)
  is mandatory — `/api/invoke` only accepts the delegated (exchanged) token,
  never the raw session token, because the session token's audience was
  never scoped to the agent.
- This intentionally mirrors AWS Bedrock AgentCore's inbound-auth model: a
  JWT authorizer sitting in front of the runtime, stateless, re-verified per
  request — not something the agent framework (LangGraph, in either case)
  implements or is trusted to enforce.

**Rule for any future work**: if you're adding an authorization/policy
check, it is a plain function/FastAPI dependency, not a graph node — even if
it gets a persona-like name ("policy agent") in telemetry or UI copy for
storytelling purposes.

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

## LangGraph: current state and why it's there at all

`backend/app/agent/graph.py` is currently **one node** (`assistant` → Claude,
`MemorySaver`-checkpointed per `thread_id`). Being honest about it: at one
node, this provides almost nothing a raw Anthropic SDK call + a dict
wouldn't. It's in place because of an explicit ask to build something that
"scales in complexity" into sidecars and multi-agent (A2A) work — i.e. it's
a bet on the roadmap below, not something earning its keep today.

### Shelved plan: multi-agent + A2A (agreed in conversation 2026-08-15, not yet built)

Goal: demonstrate real A2A (Agent2Agent protocol), not simulate it with
deterministic routing in one process. The distinguishing thing about A2A is
(a) each agent has its own independent model-backed reasoning, and (b) they
talk over the actual A2A protocol (Agent Cards, task-based JSON-RPC/HTTP,
streamed artifacts) as **separate services**, not function calls inside one
graph. A single-process graph with an if/else router and an ACL lookup does
not demonstrate that, even though it might feel like "multiple agents."

Agreed shape:

**Chat Agent graph** (this backend, what `/api/invoke` calls) — 2 nodes:
- `assistant` — model with a tool bound (e.g. `ask_task_agent`); decides
  itself, via real tool-calling, whether to delegate.
- `a2a_delegate` — *not* an LLM call. An async node acting as the A2A
  client: builds a Task, sends it to the Task Agent's A2A server (via the
  official `a2a-sdk` Python package), returns the artifact.
- Loop: `assistant` →(tool call?)→ `a2a_delegate` → back to `assistant` (to
  turn the artifact into a natural-language answer) → `END`. Same shape as
  LangGraph's prebuilt ReAct agent; the "tool" just happens to be a
  cross-process A2A call.

**Task/Specialist Agent graph** (new, separate service/process, exposes its
own A2A server) — 2 nodes:
- `task_assistant` — its own model call, reasons about which mocked tool to
  invoke (first candidate: `list_todos`).
- `execute_tool` — calls the mocked tool, loops back to `task_assistant` to
  package the result as an A2A artifact.

**Policy/ACL check** — still *not* a graph node in either graph. It's a
FastAPI-level gate in front of the Task Agent's A2A endpoint, following the
exact `inbound_auth.verify` pattern: checks the calling agent's identity
(RFC 8693 `act` claim — already implemented and decoded in
`InboundIdentity.actor_sub`) plus the delegated user's identity against an
ACL for the requested tool, before the Task Agent's own graph is touched.

**4 LangGraph nodes total, across 2 separate graphs/processes.** Do not
start building this without confirming first — it was explicitly shelved
pending go-ahead, not abandoned.

## What's built (as of 2026-08-15)

- Sign in with PingOne (OIDC Authorization Code + PKCE S256, JWE-encrypted
  session cookie).
- Authenticate Agent (Client Credentials + RFC 8693 Token Exchange).
- Inbound auth enforcement on `/api/invoke` (see above) — mandatory, not
  optional; the session token alone is rejected.
- OpenTelemetry spans + redaction + ring buffer, live in the Telemetry panel.
- Single-node LangGraph chat agent (Claude), streamed over SSE, Markdown-
  rendered, per-thread checkpointed.
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
- **`authlib.jose` is deprecated** in favor of `joserfc` (same author) as of
  authlib 1.7+ — this codebase uses `joserfc` throughout for JWT/JWE, don't
  add `authlib` back for jose functionality.
- Don't broadly `pkill -f vite` / `pkill -f uvicorn` when testing — it kills
  every matching process, including ones the user started themselves.

## Skills

- `run` — start both dev servers correctly (handles the cwd/Node gotchas
  above).
- `extend-agent-graph` — read before adding any LangGraph node, tool, or
  agent; has the full A2A plan detail beyond the summary above.
