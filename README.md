# AgentCore Console (LangGraph edition)

A PingOne-authenticated chat console for a LangGraph agent — a Python/FastAPI +
React rebuild of [digital-assistant-demo](https://github.com/bobbylite/digital-assistant-demo),
with the AWS Bedrock AgentCore backend replaced by a local LangGraph graph.

- **Sign in with PingOne** — OIDC Authorization Code + PKCE (S256), ID token
  verified against PingOne's JWKS, session sealed into an encrypted
  (JWE/A256GCM) `HttpOnly` cookie.
- **Authenticate Agent** — the agent proves its own identity via OAuth
  Client Credentials, then combines that with the user's session token via
  [RFC 8693 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693) into a
  single delegated token carrying both identities.
- **Inbound auth (AgentCore-style), scoped to where it matters** — plain
  chat only needs the signed-in session (it never touches a protected
  resource). The moment the agent needs to act *on the user's behalf* — via
  A2A delegation to the Task Agent — it needs the delegated token from
  token exchange, re-verified fresh (signature against PingOne's JWKS,
  issuer, expiry, and audience) rather than trusted from a session, mirroring
  AWS Bedrock AgentCore's inbound-auth model at that boundary. If you ask
  for something that needs delegation before authenticating, the chat
  explains it and offers an inline "Authenticate Agent" prompt right there,
  then automatically retries.
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
degraded mode: the "Sign in with PingOne" and "Authenticate Agent" buttons
show a small "not configured" hint instead of rendering.

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
   - Define the resource scope(s) it can request, and set that resource's
     **access token format to JWT** (not opaque/reference) — inbound auth
     can only cryptographically verify a JWT
   - → `AGENT_CLIENT_ID`, `AGENT_CLIENT_SECRET`, `AGENT_SCOPES` (actor
     token, step 1), `AGENT_TOKEN_EXCHANGE_SCOPE` (delegated token, step 2
     — falls back to `AGENT_SCOPES` if unset), `AGENT_EXPECTED_AUDIENCE`
     (what inbound auth checks the delegated token's `aud` against — falls
     back to `AGENT_CLIENT_ID`)

Both blocks are independent — you can enable just the sign-in app first,
add the agent app once that's working.

`SESSION_SECRET` is required by either block (any long random string; it's
hashed into the 32-byte key used for JWE session encryption). `ANTHROPIC_API_KEY`
is required for the chat panel itself (LangGraph calls `claude-sonnet-5` by
default — set `AGENT_MODEL` to change it).

If you're also running `task-agent/` (see above), its `.env` needs the
*same* `OIDC_DISCOVERY_URL` and `AGENT_EXPECTED_AUDIENCE` as `backend/.env`
— it independently re-verifies the same delegated token the Chat Agent
forwards, rather than trusting it. Set `task-agent/.env`'s
`ALLOWED_AGENT_CLIENT_ID` to `backend/.env`'s `AGENT_CLIENT_ID` — that's
what its policy ACL (`task-agent/app/policy.py`) checks against.

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
- `backend/app/auth/inbound.py` — the inbound-auth check `/api/invoke`
  (`backend/app/routes/invoke.py`) runs on every call: only the delegated
  token is accepted, re-verified fresh (signature/issuer/expiry/audience),
  never just trusted because it's sealed in a cookie.
- `backend/app/agent/graph.py` — the Chat Agent's LangGraph graph:
  `assistant` (Claude) ↔ `tools` (`ask_task_agent`), streamed over SSE once
  inbound auth passes.
- `backend/app/agent/tools.py` — `ask_task_agent`: the real A2A client call
  to the Task Agent, forwarding the same delegated token this request
  already verified.
- `shared/src/agentcore_shared/inbound_auth.py` — the inbound-auth
  verification both `backend/` and `task-agent/` use (imported, not
  duplicated) — signature/issuer/expiry/audience, checked fresh every call.
- `task-agent/app/agent_executor.py` — the Task Agent's A2A `AgentExecutor`:
  verifies the incoming token before touching its own graph
  (`task-agent/app/graph.py`), which reasons about which MCP tool to call.
- `task-agent/app/policy.py` — the tool-access ACL, checked per tool call
  via `ToolNode`'s `awrap_tool_call` hook — a plain function, not a graph
  node or an LLM's judgment call.
- `mcp-todos-server/server.py` — standalone `fastmcp` server, in-memory,
  no auth (trusted-network-only demo service).
- `frontend/src/App.tsx` — owns conversation + auth state and the SSE read
  loop; `AgentAuthButton` is the one component that owns its own local state
  machine (idle/loading/success/error), everything else is presentational.

## Extending this

See `.claude/skills/extend-agent-graph/SKILL.md` for the full pattern
(verified library APIs, identity-propagation details, and how to add a
third tool or a third agent) — the short version:

- **New mocked tool**: add it to `mcp-todos-server/server.py`, add an ACL
  entry in `task-agent/app/policy.py`. `MultiServerMCPClient` picks it up
  automatically, nothing else changes.
- **New Chat Agent capability**: add a `@tool` in `backend/app/agent/tools.py`,
  register it in `graph.py`'s `_TOOLS` list.
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
