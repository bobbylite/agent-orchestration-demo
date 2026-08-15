# AgentCore Console (LangGraph edition)

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
- **Secret scanning in CI** — every push and PR runs
  [gitleaks](https://github.com/gitleaks/gitleaks) over the full git
  history (`.github/workflows/gitleaks.yml`), catching any credential that
  slips past `.gitignore` before it lands on `main`.

## Project layout

```
backend/            Chat Agent — FastAPI + LangGraph + OIDC + OpenTelemetry (Python 3.14, uv)
frontend/            React 19 + Vite 8 + Tailwind v4 (Node >=22.12)
task-agent/          Specialist Agent — separate process, own A2A server
mcp-todos-server/    Standalone MCP server (mocked todos tool)
shared/              Inbound-auth verification shared by backend + task-agent
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
uv sync
uv run python server.py   # http://localhost:9000/mcp

# terminal 4
cd task-agent
cp .env.example .env   # OIDC_DISCOVERY_URL / AGENT_EXPECTED_AUDIENCE
                        # must match backend/.env's values exactly
uv sync
uv run uvicorn app.main:app --port 9010
```

## PingOne setup

You need **two** PingOne applications:

1. **User sign-in app** (OIDC Web App)
   - Grant type: Authorization Code, **PKCE required (S256)**
   - Redirect URI: `http://localhost:8000/api/auth/callback` (dev) — or
     `http://localhost:8080/api/auth/callback` if running via `docker compose`
   - Post-logout redirect URI: your frontend origin
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

Both blocks are independent — you can enable just the sign-in app first,
add the agent app once that's working.

`SESSION_SECRET` is required by either block (any long random string; it's
hashed into the 32-byte key used for JWE session encryption). `ANTHROPIC_API_KEY`
is required for the chat panel itself (LangGraph calls `claude-sonnet-5` by
default — set `AGENT_MODEL` to change it).

If you're also running `task-agent/` (see above), its `.env` needs the
*same* `OIDC_DISCOVERY_URL`, `AGENT_EXPECTED_AUDIENCE`, `TODOS_READ_SCOPE`,
and `TODOS_WRITE_SCOPE` as `backend/.env` — it independently re-verifies
the delegated token the Chat Agent forwards, including its `scope` claim,
rather than trusting it. Set `task-agent/.env`'s `ALLOWED_AGENT_CLIENT_ID`
to `backend/.env`'s `AGENT_CLIENT_ID` — that's what its policy check
(`task-agent/app/policy.py`) checks identity against, alongside scope.

## Docker

```bash
cp backend/.env.example backend/.env   # fill in credentials
docker compose up --build
```

This serves the built frontend and proxies `/api/*` to the backend from a
single nginx origin (`http://localhost:8080`) — register that origin's
`/api/auth/callback` with PingOne, and set `PUBLIC_ORIGIN=http://localhost:8080`
(or your real domain) before starting. Keeping frontend and backend behind
one public origin means the session cookie stays host-scoped without any
cross-site cookie configuration.

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
- `shared/src/agentcore_shared/inbound_auth.py` — the inbound-auth
  verification both `backend/` and `task-agent/` use (imported, not
  duplicated) — signature/issuer/expiry/audience/scope, checked fresh every call.
- `task-agent/app/agent_executor.py` — the Task Agent's A2A `AgentExecutor`:
  verifies the incoming token (and its scope) before touching its own graph
  (`task-agent/app/graph.py`), which reasons about which MCP tool to call.
- `task-agent/app/policy.py` — the tool-access check: identity ACL *and*
  the token's granted scope, both required, checked per tool call via
  `ToolNode`'s `awrap_tool_call` hook — a plain function, not a graph node
  or an LLM's judgment call.
- `mcp-todos-server/server.py` — standalone `fastmcp` server, in-memory,
  no auth (trusted-network-only demo service).
- `frontend/src/App.tsx` — owns conversation + auth state and the SSE read
  loop; `InlineAgentApprovalPrompt.tsx` is scoped per pending action and
  owns its own local state machine (idle/loading/error), everything else is
  presentational.

## Extending this

See `.claude/skills/extend-agent-graph/SKILL.md` for the full pattern
(verified library APIs, identity-propagation details, and how to add a
third tool or a third agent) — the short version:

- **New mocked tool**: add it to `mcp-todos-server/server.py`, add identity
  + required-scope entries in `task-agent/app/policy.py`. `MultiServerMCPClient`
  picks it up automatically, nothing else changes.
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
