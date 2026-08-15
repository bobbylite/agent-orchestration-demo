---
name: run
description: Launch the Agent Orchestration Console (Chat Agent backend + Vite frontend + Task Agent + MCP todos server + its own UI) for local development, or verify it's running. Use whenever asked to run, start, test, or screenshot this app.
---

# Running Agent Orchestration Console locally

Up to six services, six terminals (or background Bash calls) — the
first two are the minimum for basic chat; add the rest if you need the
A2A delegation (todo list questions) or the Todos MCP server's own UI to
work. Known failure modes below have been hit before — check them first
before debugging further.

## Chat Agent backend (FastAPI + LangGraph)

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

- **Must run with cwd = `backend/`.** `uv run` resolves against
  `backend/pyproject.toml` — running from the repo root fails with
  `error: Failed to spawn: uvicorn / Caused by: No such file or directory`.
- Requires `backend/.env` (copy from `backend/.env.example`) for PingOne
  sign-in and any agent action approval to work. Without it, `/api/config`
  reports `oidc_enabled`/`agent_enabled` as `false` and the sign-in button
  shows a "not configured" hint — expected degraded mode, not a bug. Plain
  chat needs only sign-in; approval prompts only appear once you ask for
  something that needs delegation (see "Verify the full stack" below).
- Verify: `curl http://localhost:8000/api/health` → `{"status":"ok"}`.

## Frontend (Vite)

```bash
cd frontend
npm run dev   # http://localhost:5173, proxies /api/* to :8000
```

- **Requires Node >=22.12** — Vite 8's rolldown bundler needs it; below
  that, `npm install` silently fails to fetch the platform-specific
  `@rolldown/binding-*` package and `npm run build`/`dev` crashes with
  `Cannot find module '@rolldown/binding-darwin-*'` (or your platform's
  equivalent). Check with `node --version`; fix with `brew upgrade node`
  (or your version manager).
- Dev server proxies `/api/*` to `:8000` so the session cookie stays
  same-origin — don't call the backend on a different port directly from
  the browser during local dev, cookies won't line up cleanly otherwise.

## Todos MCP server (FastAPI + fastmcp, needed for A2A delegation and its own UI)

```bash
cd mcp-todos-server
uv run uvicorn app.main:app --reload --port 9000
```

- One process serves three things: the MCP endpoint itself (`/mcp`, what
  task-agent calls), this service's own web UI's API (`/api/auth`,
  `/api/todos`, `/api/audit`), and `/api/health`.
- In-memory, seeded with 2 example todos. No more "no auth" fallback —
  every MCP tool call now independently re-verifies a delegated bearer
  token (same as task-agent does) and records an audit entry, success or
  denied. `curl -o /dev/null -w '%{http_code}' http://localhost:9000/mcp`
  → `406` is correct (bare GET without proper MCP headers) — that means
  it's up, not broken. `curl http://localhost:9000/api/health` →
  `{"status":"ok"}`.
- Requires `mcp-todos-server/.env` (copy from `.env.example`) for its own
  "Sign in with PingOne" (a **third**, dedicated PingOne app — not the
  same one `backend/.env` uses) and for verifying delegated tokens
  forwarded by task-agent. Without it, sign-in shows the same "not
  configured" hint pattern as `backend/`, and the MCP tools reject every
  call (no configured audience to verify against) — expected degraded
  mode.

## Todos MCP server's own frontend (Vite)

```bash
cd mcp-todos-server/frontend
npm run dev   # http://localhost:5174, proxies /api/* to :9000
```

- Separate port from the main chat frontend (`:5173`) so both can run
  side by side. Same Node >=22.12 requirement and same-origin-proxy
  reasoning as the main frontend above.
- Shows the todo list (tagged Human vs Agent) and the OBO audit log; both
  panels require signing in first.

## Task Agent (separate process, only needed for A2A delegation)

```bash
cd task-agent
uv run uvicorn app.main:app --port 9010
```

- Requires `task-agent/.env` (copy from `.env.example`). Its
  `OIDC_DISCOVERY_URL`/`AGENT_EXPECTED_AUDIENCE` **must match
  `backend/.env`'s values exactly** — it independently re-verifies the same
  delegated token the Chat Agent forwards, see `CLAUDE.md` "Identity
  propagation". A mismatch fails with a genuinely correct
  `audience_mismatch`, not a bug.
- Needs `mcp-todos-server` reachable by the time a task actually runs, not
  necessarily at its own startup — it no longer fetches MCP tools once at
  boot; it rebuilds its MCP connection fresh on every task, forwarding
  that request's verified bearer token as an `Authorization` header so
  `mcp-todos-server` can independently verify it too and attribute the
  call to the real human (OBO).
- Verify: `curl http://localhost:9010/.well-known/agent-card.json` returns
  the Task Agent's AgentCard JSON.

## Verify the full stack

1. `curl http://localhost:8000/api/health` → `{"status":"ok"}`
2. Open http://localhost:5173 — "Sign in with PingOne" should render (not
   the "not configured" hint) if `.env` is populated.
3. `curl http://localhost:5173/api/config` → confirms the proxy is wired.
4. With all four core services up: sign in, ask "what's on my todo list?"
   — this should prompt an inline "Approve Agent Action" for `todos:read`;
   approve it and the answer should reflect real MCP data (seeded: "Buy
   milk", "Renew passport"), with the Telemetry panel showing an
   `agent.a2a_delegate` span. Then ask it to complete one — this prompts
   *again*, for `todos:write` specifically; approving read earlier doesn't
   cover it.
5. With `mcp-todos-server`'s own frontend also up: open
   http://localhost:5174, sign in (its own PingOne app), and confirm the
   todos from step 4 show up tagged "Agent" with the audit log showing
   `"OBO — Agent <name> used add_todo on behalf of <you>"` entries.

## Type-check / lint / build (frontend)

```bash
cd frontend
npx tsc -b && npm run lint && npm run build
```

## Cleanup

If you started background instances for testing, stop them by PID or a
narrow `pkill` pattern — **not** a bare `pkill -f vite` / `pkill -f
uvicorn`, which kills every matching process including ones the user
started themselves in their own terminal. This has happened before. Note
`backend/`, `task-agent/`, and `mcp-todos-server/` all run `uvicorn
app.main:app` — match on `--port 8000` vs `--port 9010` vs `--port 9000`
to tell them apart, not just the module path. Same for the two Vite
frontends: `--port 5173` (chat) vs `--port 5174` (todos).
