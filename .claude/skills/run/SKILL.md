---
name: run
description: Launch the AgentCore Console (FastAPI backend + Vite frontend) for local development, or verify it's running. Use whenever asked to run, start, test, or screenshot this app.
---

# Running AgentCore Console locally

Two servers, two terminals (or two background Bash calls). Both known
failure modes below have been hit before — check them first before
debugging further.

## Backend (FastAPI + LangGraph)

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

- **Must run with cwd = `backend/`.** `uv run` resolves against
  `backend/pyproject.toml` — running from the repo root fails with
  `error: Failed to spawn: uvicorn / Caused by: No such file or directory`.
- Requires `backend/.env` (copy from `backend/.env.example`) for the
  PingOne sign-in, Authenticate Agent, and chat to actually work. Without
  it, `/api/config` reports `oidc_enabled`/`agent_enabled` as `false` and
  the UI shows "not configured" hints instead of the buttons — that's
  expected degraded mode, not a bug.
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

## Verify the full stack

1. `curl http://localhost:8000/api/health` → `{"status":"ok"}`
2. Open http://localhost:5173 — "Sign in with PingOne" should render (not
   the "not configured" hint) if `.env` is populated.
3. `curl http://localhost:5173/api/config` → confirms the proxy is wired.

## Type-check / lint / build (frontend)

```bash
cd frontend
npx tsc -b && npm run lint && npm run build
```

## Cleanup

If you started background instances for testing, stop them by PID or a
narrow `pkill` pattern — **not** a bare `pkill -f vite` / `pkill -f
uvicorn`, which kills every matching process including ones the user
started themselves in their own terminal. This has happened before.
