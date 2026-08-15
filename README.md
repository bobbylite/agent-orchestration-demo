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
- **Inbound auth (AgentCore-style)** — `/api/invoke` accepts *only* the
  delegated token from token exchange, never the plain session token, and
  re-verifies it fresh on every call (signature against PingOne's JWKS,
  issuer, expiry, and audience) rather than trusting a session. This mirrors
  AWS Bedrock AgentCore's inbound-auth model, where every request carries a
  bearer JWT a JWT authorizer verifies statelessly — there's no notion of
  "already logged in" at the agent boundary.
- **OpenTelemetry** — every credential-bearing operation (login, logout,
  agent auth, token exchange, inbound-auth verification, agent invocation)
  emits a span. A redaction filter drops any attribute whose key looks like
  `token`, `secret`, `password`, or `authoriz*` before it's ever stored — the
  token itself never appears on a span.
- **LangGraph agent** — a single-node graph today (`assistant` → Claude),
  checkpointed per conversation thread, designed to grow into a multi-node
  graph with sidecar tool calls and multi-agent (A2A) handoff.

## Project layout

```
backend/    FastAPI + LangGraph + OIDC + OpenTelemetry (Python 3.14, uv)
frontend/   React 19 + Vite 8 + Tailwind v4 (Node >=22.12)
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
- `backend/app/agent/graph.py` — the LangGraph graph `/api/invoke` streams
  over SSE once inbound auth passes.
- `frontend/src/App.tsx` — owns conversation + auth state and the SSE read
  loop; `AgentAuthButton` is the one component that owns its own local state
  machine (idle/loading/success/error), everything else is presentational.

## Extending this — sidecars and A2A

The LangGraph graph is intentionally a single node right now. The natural
next steps, none of which require touching the auth/telemetry layers:

- **Sidecars**: add more nodes to `build_graph()` that call out to an HTTP
  or MCP-backed service, with `agent.invoke` spans as the template for
  instrumenting them (identity + duration, never payload contents).
- **Multi-agent / A2A**: LangGraph's `Command`-based handoff patterns
  compose naturally with this graph as a subgraph; the compiled graph here
  can also be exposed behind an A2A-compatible server later without
  changing how `/api/invoke` authenticates callers.
- **Settings persistence**: the reference app's runtime-editable Settings
  panel (`/api/config` + `/api/settings`) isn't built yet — `app/config.py`
  is structured so adding a writable-at-runtime layer over `Settings` is a
  contained change.
