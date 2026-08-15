---
name: run
description: Launch the AgentCore Console (Chat Agent backend + Vite frontend + Task Agent + MCP todos server) for local development, or verify it's running. Use whenever asked to run, start, test, or screenshot this app.
---

# Running AgentCore Console locally

Up to four services, four terminals (or background Bash calls) — the
first two are the minimum for basic chat; add the last two if you need
the A2A delegation (todo list questions) to work. Known failure modes
below have been hit before — check them first before debugging further.

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

## Todos MCP server (standalone, only needed for A2A delegation)

```bash
cd mcp-todos-server
uv run python server.py   # http://localhost:9000/mcp
```

- In-memory, no auth, seeded with 2 example todos. `curl -o /dev/null -w
  '%{http_code}' http://localhost:9000/mcp` → `406` is correct (bare GET
  without proper MCP headers) — that means it's up, not broken.

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
- Needs `mcp-todos-server` running first (it fetches MCP tools at startup).
- Verify: `curl http://localhost:9010/.well-known/agent-card.json` returns
  the Task Agent's AgentCard JSON.

## Verify the full stack

1. `curl http://localhost:8000/api/health` → `{"status":"ok"}`
2. Open http://localhost:5173 — "Sign in with PingOne" should render (not
   the "not configured" hint) if `.env` is populated.
3. `curl http://localhost:5173/api/config` → confirms the proxy is wired.
4. With all four services up: sign in, ask "what's on my todo list?" — this
   should prompt an inline "Approve Agent Action" for `todos:read`; approve
   it and the answer should reflect real MCP data (seeded: "Buy milk",
   "Renew passport"), with the Telemetry panel showing an `agent.a2a_delegate`
   span. Then ask it to complete one — this prompts *again*, for
   `todos:write` specifically; approving read earlier doesn't cover it.

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
`backend/` and `task-agent/` both run `uvicorn app.main:app` — match on
`--port 8000` vs `--port 9010` to tell them apart, not just the module path.
