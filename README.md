# Agent Orchestration Console (LangGraph edition)

A PingOne-authenticated chat console for a LangGraph agent — a Python/FastAPI +
React rebuild of [digital-assistant-demo](https://github.com/bobbylite/digital-assistant-demo),
with the AWS Bedrock AgentCore backend replaced by a local LangGraph graph.

- **Sign in with PingOne** — OIDC Authorization Code + PKCE (S256), ID token
  verified against PingOne's JWKS, session sealed into an encrypted
  (JWE/A256GCM) `HttpOnly` cookie. Sufficient on its own for plain chat.
- **Approve Agent Action, per action, not once** — the agent proves its own
  identity via OAuth Client Credentials, then combines that with the user's
  session token via [RFC 8693 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693)
  into a delegated token — but scoped to exactly one capability at a time
  (`todos:read` or `todos:write`), requested the first time it's actually
  needed. Approving read access never grants write access; asking the agent
  to change something after only approving reading prompts again, for write
  specifically.
- **Inbound auth (AgentCore-style), scoped to where it matters** — plain
  chat only needs the signed-in session (it never touches a protected
  resource). The moment the agent needs to act *on the user's behalf* — via
  A2A delegation to the Task Agent — it needs a delegated token scoped for
  that specific action, re-verified fresh (signature against PingOne's
  JWKS, issuer, expiry, audience, **and scope**) rather than trusted from a
  session, mirroring AWS Bedrock AgentCore's inbound-auth model at that
  boundary. If you ask for something that needs a scope you haven't
  approved yet, the chat explains it and offers an inline "Approve Agent
  Action" prompt right there, scoped to that one action, then automatically
  retries.
- **OpenTelemetry** — every credential-bearing operation (login, logout,
  agent auth, token exchange, inbound-auth verification, agent invocation)
  emits a span. A redaction filter drops any attribute whose key looks like
  `token`, `secret`, `password`, or `authoriz*` before it's ever stored — the
  token itself never appears on a span.
- **LangGraph agent, real A2A delegation** — the Chat Agent reasons about
  whether a request needs a specialist (e.g. anything about a todo list)
  and, if so, delegates to a separate **Task Agent** process over the actual
  [A2A protocol](https://a2a-protocol.org) (Agent Cards, task-based
  JSON-RPC, not an in-process function call). The Task Agent independently
  re-verifies the same delegated token — no implicit trust between
  services — then reasons about which tool to use against a real
  [MCP](https://modelcontextprotocol.io) server, gated by its own policy ACL.
- **The MCP server has its own PingOne-gated UI and an OBO audit log** —
  `mcp-todos-server/` is no longer a bare, unauthenticated tool server: it
  independently re-verifies the same delegated token one more hop out (the
  Task Agent forwards it unchanged), tags every todo with who actually
  created it (a signed-in human directly, or an agent on a human's behalf),
  and records every call — allowed or denied — to an in-memory audit log
  its own UI renders live: *"OBO — Agent Task Agent used add_todo on
  behalf of robert.luisi@pingidentity.com"*.
- **Secret scanning in CI** — every push and PR runs
  [gitleaks](https://github.com/gitleaks/gitleaks) over the full git
  history (`.github/workflows/gitleaks.yml`), catching any credential that
  slips past `.gitignore` before it lands on `main`.

## Project layout

```
backend/                     Chat Agent — FastAPI + LangGraph + OIDC + OpenTelemetry (Python 3.14, uv)
frontend/                    React 19 + Vite 8 + Tailwind v4 (Node >=22.12)
task-agent/                  Specialist Agent — separate process, own A2A server
mcp-todos-server/            MCP server (mocked todos tool) + its own PingOne-gated UI's API + OBO audit log
mcp-todos-server/frontend/   React 19 + Vite 8 + Tailwind v4 — todo list (human/agent tagged) + audit log
shared/                      Inbound-auth verification shared by backend + task-agent + mcp-todos-server
```

## Local development

**Backend**

```bash
cd backend
cp .env.example .env   # fill in PingOne + Anthropic credentials (see below)
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

**Frontend** (needs Node >=22.12 — Vite 8's rolldown bundler requires it)

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173, proxies /api/* to :8000
```

Open http://localhost:5173. With no `.env` configured, the app runs in
degraded mode: "Sign in with PingOne" shows a small "not configured" hint
instead of rendering, and the chat has no way to obtain a delegated token
(so anything needing one — A2A delegation — will keep prompting to approve
without ever succeeding).

**Todos MCP server + Task Agent** (optional — only needed for the chat to
answer questions about a todo list; everything else works without them)

```bash
# terminal 3
cd mcp-todos-server
cp .env.example .env   # PingOne app #3 (its own UI) + must match backend/.env's
                        # AGENT_EXPECTED_AUDIENCE / TODOS_READ_SCOPE / TODOS_WRITE_SCOPE
uv sync
uv run uvicorn app.main:app --port 9000   # MCP endpoint at /mcp, its UI's API at /api/*

# terminal 4
cd task-agent
cp .env.example .env   # OIDC_DISCOVERY_URL / AGENT_EXPECTED_AUDIENCE
                        # must match backend/.env's values exactly
uv sync
uv run uvicorn app.main:app --port 9010
```

**Todos MCP server's own UI** (optional — lets you watch the todo list and
the OBO audit log fill in live as you use the chat)

```bash
# terminal 5
cd mcp-todos-server/frontend
npm install
npm run dev   # http://localhost:5174, proxies /api/* to :9000
```

**Or skip the terminals** — `.vscode/launch.json` has one debugpy/node-terminal
config per service (each with its `.env` wired up via `envFile` where it
applies) and three compounds to start them together: **🚀 Launch
Everything** (all five services), **💬 Chat Only (no A2A)** (just
`backend`/`frontend`), and **🗂 Todos MCP Server (server + its own UI)**
(just `mcp-todos-server`/`mcp-todos-server/frontend`, for working on that
service in isolation). Pick one from VS Code's Run and Debug panel. Task
Agent's config has a `preLaunchTask` that waits for `mcp-todos-server` to
accept connections before it starts, since a task issued before that would
fail regardless of which order the two were clicked in.

## PingOne setup

You need **three** PingOne applications:

1. **User sign-in app** (OIDC Web App)
   - Grant type: Authorization Code, **PKCE required (S256)**
   - Redirect URI: `http://localhost:8000/api/auth/callback` (dev) — or
     `http://localhost:8080/api/auth/callback` if running via `docker compose`
   - Post-logout redirect URI: your frontend origin
   - The **resource behind this app's access token must also issue JWT**
     (not opaque/reference) tokens — even though this app itself only ever
     needs to verify an *ID* token (always a JWT), its *access* token is
     later sent as Token Exchange's `subject_token` (`/api/auth/agent-token`,
     step 2) when approving an agent action, and PingOne can only read
     claims off a JWT there. Opaque/reference fails with a clear PingOne
     error — `Cannot parse token claims for request param 'subject_token'`
     — not a silent success. In PingOne this is usually under
     **Connections → Resources → [the resource this app's tokens are
     minted against, often the default "PingOne API" one] → Access Token
     Type**.
   - → `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
     `OIDC_POST_LOGOUT_REDIRECT_URI`

2. **Agent worker app** (Service/Worker app)
   - Grant types: **Client Credentials** and **Token Exchange**
   - Token endpoint auth method: `client_secret_basic`
   - Define `todos:read` and `todos:write` (or your own names — just keep
     `TODOS_READ_SCOPE`/`TODOS_WRITE_SCOPE` matching) as scopes on the
     resource, and set that resource's **access token format to JWT** (not
     opaque/reference) — inbound auth can only cryptographically verify a JWT
   - → `AGENT_CLIENT_ID`, `AGENT_CLIENT_SECRET`, `AGENT_SCOPES` (the agent's
     own actor-token scope, step 1), `AGENT_EXPECTED_AUDIENCE` (what inbound
     auth checks a delegated token's `aud` against — falls back to
     `AGENT_CLIENT_ID`), `TODOS_READ_SCOPE`/`TODOS_WRITE_SCOPE` (the two
     per-action delegation scopes token exchange requests, step 2 — one
     independent exchange call per scope, not a single blanket one)

3. **Todos MCP server sign-in app** (OIDC Web App) — a dedicated app for
   `mcp-todos-server/`'s own UI, separate from app #1. Same shape as #1:
   - Grant type: Authorization Code, **PKCE required (S256)**
   - Redirect URI: `http://localhost:9000/api/auth/callback` (dev) — or
     `http://localhost:8081/api/auth/callback` if running via `docker compose`
   - Post-logout redirect URI: `mcp-todos-server/`'s frontend origin
   - → `mcp-todos-server/.env`'s `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`,
     `OIDC_CLIENT_SECRET`, `OIDC_POST_LOGOUT_REDIRECT_URI` — its own values,
     not shared with app #1's

All three blocks are independent — you can enable just the sign-in app
first, add the others as you go.

`SESSION_SECRET` is required by any of them (any long random string; it's
hashed into the 32-byte key used for JWE session encryption — use a
*different* value per service, they don't share sessions). `ANTHROPIC_API_KEY`
is required for the chat panel itself (LangGraph calls `claude-sonnet-5` by
default — set `AGENT_MODEL` to change it).

If you're also running `task-agent/` and `mcp-todos-server/` (see above),
**both** need the *same* `OIDC_DISCOVERY_URL`, `AGENT_EXPECTED_AUDIENCE`,
`TODOS_READ_SCOPE`, and `TODOS_WRITE_SCOPE` as `backend/.env` — the Chat
Agent's delegated token is forwarded unchanged all the way through
task-agent to mcp-todos-server, and each hop independently re-verifies it,
including its `scope` claim, rather than trusting the previous hop. Set
`task-agent/.env`'s *and* `mcp-todos-server/.env`'s `ALLOWED_AGENT_CLIENT_ID`
to `backend/.env`'s `AGENT_CLIENT_ID` — that's what each service's own
policy check (`task-agent/app/policy.py`, `mcp-todos-server/app/policy.py`)
checks identity against, alongside scope. Three copies of the same "must
match exactly" values now — a mismatch anywhere fails with a correct
`audience_mismatch`/`issuer_mismatch`/policy denial, not a bug, but easy to
forget to update everywhere.

## Docker

```bash
cp backend/.env.example backend/.env             # fill in credentials
cp mcp-todos-server/.env.example mcp-todos-server/.env   # optional — its own UI + OBO audit log
docker compose up --build
```

This serves the built chat frontend and proxies `/api/*` to the backend
from one nginx origin (`http://localhost:8080`) — register that origin's
`/api/auth/callback` with PingOne app #1, and set
`PUBLIC_ORIGIN=http://localhost:8080` (or your real domain) before
starting. `mcp-todos-server/`'s own UI gets the same treatment on a
second, independent origin (`http://localhost:8081`, service
`mcp-todos-server-frontend`) — register *that* origin's
`/api/auth/callback` with PingOne app #3, and set
`MCP_TODOS_PUBLIC_ORIGIN=http://localhost:8081` (or your real domain).
Keeping each frontend/backend pair behind its own single public origin
means each session cookie stays host-scoped without any cross-site cookie
configuration.

## How the pieces fit together

- `backend/app/telemetry.py` — the `RecordingSpanProcessor` ring buffer and
  redaction filter every other module wraps calls in via `with_span(...)`.
- `backend/app/auth/oidc.py`, `pkce.py`, `session.py` — PingOne sign-in.
- `backend/app/auth/agent_auth.py` — Client Credentials + Token Exchange.
- `backend/app/auth/routes.py` — wires the above into
  `/api/auth/{login,callback,logout,agent-token,me}`.
- `backend/app/auth/inbound.py` — the inbound-auth check `backend/app/routes/invoke.py`
  runs for whichever exchanged-scope tokens are present: each re-verified
  fresh (signature/issuer/expiry/audience), never just trusted because it's
  sealed in a cookie.
- `backend/app/agent/graph.py` — the Chat Agent's LangGraph graph:
  `assistant` (Claude) ↔ `tools` (`ask_task_agent_read`/`ask_task_agent_write`),
  streamed over SSE once inbound auth passes for the base session.
- `backend/app/agent/tools.py` — the two delegation tools: real A2A client
  calls to the Task Agent, each forwarding the delegated token for its own
  scope specifically (`config["configurable"]["bearer_tokens"][scope]`).
- `shared/src/agentorchestration_shared/inbound_auth.py` — the inbound-auth
  verification `backend/`, `task-agent/`, *and* `mcp-todos-server/` all use
  (imported, not duplicated) — signature/issuer/expiry/audience/scope,
  checked fresh every call, plus a best-effort `email` claim and the
  `aud` actually verified against, both used for the OBO audit log.
- `task-agent/app/agent_executor.py` — the Task Agent's A2A `AgentExecutor`:
  verifies the incoming token (and its scope) before touching its own graph
  (`task-agent/app/graph.py`), which reasons about which MCP tool to call.
  Rebuilds its MCP connection fresh per task (not cached at startup),
  forwarding that request's own verified bearer token as an
  `Authorization` header so `mcp-todos-server` can independently verify it
  too, one hop further.
- `task-agent/app/policy.py` — the tool-access check: identity ACL *and*
  the token's granted scope, both required, checked per tool call via
  `ToolNode`'s `awrap_tool_call` hook — a plain function, not a graph node
  or an LLM's judgment call.
- `mcp-todos-server/app/mcp_server.py` — the MCP tool surface itself. Every
  tool independently re-verifies the forwarded bearer token and re-checks
  policy (`mcp-todos-server/app/policy.py`, same shape as task-agent's) —
  this service doesn't trust that task-agent already gated the call.
- `mcp-todos-server/app/audit.py` — the OBO audit log: an in-memory ring
  buffer (same shape as `backend/app/telemetry.py`'s span buffer), one
  entry per tool call, human or agent, allowed or denied, attributing
  agent calls to the real human the token was issued for.
- `mcp-todos-server/app/identity.py` — resolves a human-readable label for
  a `sub` for that audit log: the verified token's own `email` claim
  first, else a `sub -> {email, name}` cache populated whenever a human
  signs into `mcp-todos-server/`'s own UI, else the raw `sub`.
- `mcp-todos-server/app/store.py` — the in-memory todo store, tagging each
  todo `created_by: "human" | "agent"` plus who (`creator_sub`/
  `creator_label`) — shared by both the MCP tools (agent path) and
  `mcp-todos-server/app/routes/todos.py` (human path, session-cookie
  authenticated, same "a verified session is enough for a non-delegated
  action" tier the chat app uses for plain chat).
- `frontend/src/App.tsx` — owns conversation + auth state and the SSE read
  loop; `InlineAgentApprovalPrompt.tsx` is scoped per pending action and
  owns its own local state machine (idle/loading/error), everything else is
  presentational.
- `mcp-todos-server/frontend/src/App.tsx` — its own, much smaller
  equivalent: a todo list (`TodoPanel.tsx`, tagged Human/Agent via
  `CreatorBadge.tsx`) and the audit log itself (`AuditLogPanel.tsx`,
  rendering the OBO/direct sentence per entry), both gated on that
  service's own sign-in state.

## Extending this

See `.claude/skills/extend-agent-graph/SKILL.md` for the full pattern
(verified library APIs, identity-propagation details, and how to add a
third tool or a third agent) — the short version:

- **New mocked tool**: add it to `mcp-todos-server/app/mcp_server.py`
  (verify the caller + check policy + record an audit entry, same shape as
  the existing three tools), add identity + required-scope entries in
  **both** `task-agent/app/policy.py` and `mcp-todos-server/app/policy.py`
  — each service gates independently, neither trusts the other's gate.
  `MultiServerMCPClient` picks up the new tool automatically, nothing else
  changes.
- **New Chat Agent capability**: add a `@tool` in `backend/app/agent/tools.py`,
  register it in `graph.py`'s `_TOOLS` list. New scope needed? Add it to
  `Settings.allowed_delegation_scopes` (`backend/app/config.py`) first —
  `/api/auth/agent-token` refuses to exchange for anything not in that set.
- **A third agent**: same shape as `task-agent/` — own `AgentCard`, own
  `AgentExecutor` doing inbound auth first, own graph. Whether it shares the
  Chat Agent's token audience or needs a fresh (nested) token exchange is
  the main design question, not yet resolved either way.
- **Settings persistence**: the reference app's runtime-editable Settings
  panel (`/api/config` + `/api/settings`) isn't built yet — `app/config.py`
  is structured so adding a writable-at-runtime layer over `Settings` is a
  contained change.
- **Cross-service telemetry**: `task-agent/` doesn't have its own
  `RecordingSpanProcessor`/`/telemetry` endpoint yet, so its internal steps
  aren't visible in the frontend's Telemetry panel (only the Chat Agent's
  `agent.a2a_delegate` span, which wraps the whole round-trip, is).
  `mcp-todos-server/`'s audit log (`app/audit.py`) is a separate,
  purpose-built mechanism for a different job — attributing OBO actions to
  a human, not general span tracing — not a stand-in for this.
