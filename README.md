# Agent Orchestration Console (LangGraph edition)

A production-shaped **multi-agent system**, not a demo dressed up to look like one:
two independent [LangGraph](https://www.langchain.com/langgraph) agents, running as
separate processes, talking to each other over the real
[A2A protocol](https://a2a-protocol.org) — with a chained OAuth 2.0 delegation model
that mirrors AWS Bedrock AgentCore's inbound-auth pattern, a live
LLM-as-judge evaluator-optimizer loop watching every answer, real
[MCP](https://modelcontextprotocol.io) tool calls, and every credential-bearing hop
narrated live on an animated architecture diagram via OpenTelemetry. A
Python/FastAPI + React rebuild of
[digital-assistant-demo](https://github.com/bobbylite/digital-assistant-demo), with
the AWS Bedrock AgentCore backend replaced by local LangGraph graphs.

Nothing here is simulated for effect: no agent trusts the previous hop's say-so, no
token is forwarded unchanged, and no "judge" node is a rubber stamp — each of those
claims is independently, cryptographically, or structurally enforced, and this
README walks through exactly how.

## Further reading

This project’s orchestration design follows the coordinating-agent and
specialist-agent patterns described in Microsoft’s
[AI agent design patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)
and LangChain’s
[multi-agent systems guide](https://docs.langchain.com/oss/python/langchain/multi-agent).
The Task Agent’s quality loop is an evaluator-optimizer pattern: a judge
reviews the specialist’s proposed answer and sends concrete feedback for a
bounded retry, following the Azure Logic Apps
[evaluator-optimizer pattern](https://azure.github.io/logicapps-labs/docs/logicapps-ai-course/build_multi_agent_systems/evaluator-optimizer/).

## Highlights

- **Real multi-agent orchestration — two processes, one real wire protocol.**
  The Chat Agent and Task Agent are genuinely separate LangGraph graphs in
  genuinely separate OS processes, each with its own PingOne identity,
  exchanging Agent Cards and task-based JSON-RPC over the actual A2A
  protocol — not an in-process function call wearing an agent costume. The
  Chat Agent decides, via real tool-calling, whether a request needs a
  specialist; the Task Agent independently re-verifies everything it
  receives before touching its own graph or a real MCP server.
- **Chained RFC 8693 delegation — four token exchanges, six PingOne
  identities, zero pass-through tokens.** The Chat Agent proves its own
  identity (OAuth Client Credentials) then combines that with the user's
  session token via [RFC 8693 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693)
  into a *generic* delegation credential addressed to the Task Agent — it
  never requests `todos:read`/`todos:write` itself. The Task Agent, in
  turn, proves *its own* identity and performs its *own* Token Exchange —
  combining the credential it just received with its own identity — scoped
  to exactly the todos capability the specific tool call needs, freshly,
  every call. That's what's actually calling the todos MCP server: a
  credential minted by the service about to use it, not one forwarded
  unchanged from two hops away. Approving is a single "Approve Agent
  Action" per chat session; which specific capability gets requested is
  decided downstream, per action, with nothing cached across different
  scopes ("step-up" scoping for free).
- **An evaluator-optimizer judge, watching every answer.** After the Task
  Agent proposes an answer, a second LLM call — with its own structured
  `pass`/`fail` contract (Pydantic + `with_structured_output`), and
  swappable between Claude and Groq's free tier with one `.env` line —
  grades it against what was actually delegated and loops back with
  concrete feedback on a fail, capped at a configurable number of
  retries, and fails *open* (never blocks a real answer) if the judge
  call itself breaks. It's correctly a graph node, not a gate — a
  deliberate, documented departure from how every auth check in this repo
  works, because a quality judgment and an authorization decision are not
  the same kind of thing.
- **Inbound auth (AgentCore-style) at every hop** — plain chat only needs
  the signed-in session (it never touches a protected resource). The
  moment the agent needs to act *on the user's behalf*, each hop in the
  delegation chain re-verifies fresh (signature against PingOne's JWKS,
  issuer, expiry, audience) rather than trusting the previous hop's
  say-so, mirroring AWS Bedrock AgentCore's inbound-auth model. If you ask
  for something the agent doesn't yet have a delegation credential for,
  the chat explains it and offers an inline "Approve Agent Action" prompt,
  then automatically retries.
- **OpenTelemetry across every agent, replayed on a live architecture
  diagram.** Both the Chat Agent and the Task Agent run their own
  `RecordingSpanProcessor` — every credential-bearing operation (login,
  agent auth, token exchange, inbound-auth verification, agent invocation,
  and now each judge attempt's pass/fail verdict) emits a real span, with a
  redaction filter dropping any attribute whose key looks like `token`,
  `secret`, `password`, or `authoriz*` before it's ever stored, so a
  credential can never land on a span even by accident. The frontend polls
  both services and replays the spans live onto an animated
  [React Flow](https://reactflow.dev) diagram of the actual identity/data
  flow — watch a single request cross six PingOne identities and two agent
  processes, node by node, as it happens.
- **The MCP server has its own PingOne-gated UI and an OBO audit log** —
  `mcp-todos-server/` is no longer a bare, unauthenticated tool server: it
  independently re-verifies the token the Task Agent mints for it (its own
  RFC 8693 exchange, not a forwarded token — see "Chained RFC 8693
  delegation" above), tags every todo with who actually created it (a
  signed-in human directly, or an agent on a human's behalf), and records
  every call — allowed or denied — to an in-memory audit log its own UI
  renders live: *"OBO — Agent Task Agent used add_todo on behalf of
  robert.luisi@pingidentity.com"*.
- **Secret scanning in CI** — every push and PR runs
  [gitleaks](https://github.com/gitleaks/gitleaks) over the full git
  history (`.github/workflows/gitleaks.yml`), catching any credential that
  slips past `.gitignore` before it lands on `main`.

## Project layout

```
backend/                     Chat Agent — FastAPI + LangGraph + OIDC + OpenTelemetry (Python 3.14, uv)
frontend/                    React 19 + Vite 8 + Tailwind v4 (Node >=22.12)
task-agent/                  Specialist Agent — separate process, own A2A server, own
                              OpenTelemetry, own evaluator-optimizer judge node
mcp-todos-server/            MCP server (mocked todos tool) + its own PingOne-gated UI's API + OBO audit log
mcp-todos-server/frontend/   React 19 + Vite 8 + Tailwind v4 — todo list (human/agent tagged) + audit log
claude-bridge/               Local stdio MCP server — makes Claude Desktop itself the
                              orchestrating agent, same identity backend/ uses (see below)
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
cp .env.example .env   # PingOne app #4 (its own UI) + own audience
                        # (its own URL) + TODOS_READ_SCOPE / TODOS_WRITE_SCOPE
uv sync
uv run uvicorn app.main:app --port 9000   # MCP endpoint at /mcp, its UI's API at /api/*

# terminal 4
cd task-agent
cp .env.example .env   # OIDC_DISCOVERY_URL must match backend/.env's; everything
                        # else here is task-agent's OWN identity (PingOne apps #5, #6)
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

You need **six** PingOne applications now — one per hop in the chained
RFC 8693 delegation (see `CLAUDE.md` "RFC 8693 chained delegation" for the
full picture of why each hop mints its own credential instead of one
token being forwarded unchanged). They break into three pairs, one pair
per service that has its own identity:

**1. User sign-in app** (OIDC Web App)
- Grant type: Authorization Code, **PKCE required (S256)**
- Redirect URI: `http://localhost:8000/api/auth/callback` (dev) — or
  `http://localhost:8080/api/auth/callback` if running via `docker compose`
- Post-logout redirect URI: your frontend origin
- The **resource behind this app's access token must also issue JWT**
  (not opaque/reference) tokens — even though this app itself only ever
  needs to verify an *ID* token (always a JWT), its *access* token is
  later sent as the very first Token Exchange's `subject_token`, and
  PingOne can only read claims off a JWT there. Opaque/reference fails
  with a clear PingOne error — `Cannot parse token claims for request
  param 'subject_token'` — not a silent success. In PingOne this is
  usually under **Connections → Resources → [the resource this app's
  tokens are minted against, often the default "PingOne API" one] →
  Access Token Type**.
- → `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
  `OIDC_POST_LOGOUT_REDIRECT_URI` (`backend/.env`)

**2. Chat Agent's own identity app** (Worker/Service app)
- Grant type: **Client Credentials** only
- Token endpoint auth method: `client_secret_basic`
- Define `agent:orchestration` (or your own name — keep
  `AGENT_OWN_SCOPE` matching) as a scope on a resource whose **audience is
  the Chat Agent's own URL** (`http://localhost:8000`) and whose access
  token format is **JWT**
- → `AGENT_CLIENT_ID`, `AGENT_CLIENT_SECRET`, `AGENT_OWN_SCOPE` (`backend/.env`)

**3. Chat Agent's delegation-exchange app** (Worker/Service app) —
deliberately a *different* app from #2: "an agent proving who it is" and
"a client trusted to mint delegated tokens" are different privilege
levels in a real deployment.
- Grant types: **Client Credentials** and **Token Exchange**
- Token endpoint auth method: `client_secret_basic`
- Define `agent:delegation` (or your own name — keep
  `AGENT_DELEGATION_SCOPE` matching) as a scope on a resource whose
  **audience is the Task Agent's own URL** (`http://localhost:9010`) and
  whose access token format is **JWT**
- → `AGENT_DELEGATION_CLIENT_ID`, `AGENT_DELEGATION_CLIENT_SECRET`,
  `AGENT_DELEGATION_SCOPE` (`backend/.env`)

**4. Todos MCP server sign-in app** (OIDC Web App) — a dedicated app for
`mcp-todos-server/`'s own UI, separate from app #1, same shape:
- Grant type: Authorization Code, **PKCE required (S256)**
- Redirect URI: `http://localhost:9000/api/auth/callback` (dev) — or
  `http://localhost:8081/api/auth/callback` if running via `docker compose`
- Post-logout redirect URI: `mcp-todos-server/`'s frontend origin
- → `mcp-todos-server/.env`'s `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`,
  `OIDC_CLIENT_SECRET`, `OIDC_POST_LOGOUT_REDIRECT_URI` — its own values,
  not shared with app #1's

**5. Task Agent's own identity app** (Worker/Service app) — same shape
as #2, one hop later:
- Grant type: **Client Credentials** only
- Token endpoint auth method: `client_secret_basic`
- Define `agent:task` (or your own name — keep `AGENT_TASK_SCOPE`
  matching) as a scope on a resource whose **audience is the Task Agent's
  own URL** (`http://localhost:9010`) and whose access token format is **JWT**
- → `TASK_AGENT_CLIENT_ID`, `TASK_AGENT_CLIENT_SECRET`, `AGENT_TASK_SCOPE`
  (`task-agent/.env`)

**6. Task Agent's MCP-exchange app** (Worker/Service app) — same shape
as #3, one hop later:
- Grant type: **Token Exchange**
- Token endpoint auth method: `client_secret_basic`
- Define `todos:read` and `todos:write` (or your own names — keep
  `TODOS_READ_SCOPE`/`TODOS_WRITE_SCOPE` matching) as scopes on a resource
  whose **audience is the MCP server's own URL**
  (`http://localhost:9000/mcp`) and whose access token format is **JWT**
- → `TODOS_MCP_CLIENT_ID`, `TODOS_MCP_CLIENT_SECRET`,
  `TODOS_READ_SCOPE`/`TODOS_WRITE_SCOPE` (`task-agent/.env`)

All six are independent — you can enable just the sign-in app first, add
the rest as you go; each pair only unlocks once both its apps are filled in.

`SESSION_SECRET` is required by either sign-in app (any long random
string; hashed into the 32-byte key used for JWE session encryption — use
a *different* value per service, they don't share sessions).
`ANTHROPIC_API_KEY` is required for the chat panel (LangGraph calls
`claude-sonnet-5` by default — set `AGENT_MODEL` to change it). Both
`backend/` and `task-agent/` can instead run their own reasoning LLM on
OpenAI or Groq: set `MODEL_PROVIDER=openai` + `OPENAI_API_KEY` + `MODEL_ID`
(e.g. `gpt-4.1`), or `MODEL_PROVIDER=groq` + `GROQ_API_KEY` + `MODEL_ID`
(e.g. `llama-3.3-70b-versatile`, free tier via console.groq.com), in
either service's `.env` independently — this only changes which model
answers, never how the agent proves its identity, which is always PingOne
either way. The Task Agent's judge has its own independent
`JUDGE_PROVIDER` switch (`anthropic`/`groq`/`openai`), so all three
agents — orchestration, task, and judge — can each be pointed at a
different provider. See CLAUDE.md's "Configurable LLM provider" section
for the full picture.

**What must match across services, and what must NOT**: `OIDC_DISCOVERY_URL`
must be identical everywhere (same PingOne tenant). Each service's own
`AGENT_EXPECTED_AUDIENCE` must be **its own URL**, not shared — that's the
whole point of the redesign, so don't try to make `backend/.env`,
`task-agent/.env`, and `mcp-todos-server/.env`'s audiences match each
other. `task-agent/.env`'s `ALLOWED_AGENT_CLIENT_ID` must be app #3's
client_id (`AGENT_DELEGATION_CLIENT_ID`) — that's whose identity PingOne
propagates onto the token task-agent receives (see `CLAUDE.md`'s
`agent_client_id`-vs-`client_id` gotcha). `mcp-todos-server/.env`'s
`ALLOWED_AGENT_CLIENT_ID` must be app #5's client_id
(`TASK_AGENT_CLIENT_ID`), for the same reason, one hop later.
`task-agent/.env` and `mcp-todos-server/.env`'s `TODOS_READ_SCOPE`/
`TODOS_WRITE_SCOPE` must match each other exactly (`backend/.env` doesn't
have these settings at all anymore).

## Claude Desktop as the orchestrator (optional)

`claude-bridge/` swaps the front door: instead of the React frontend
talking to `backend/`'s LangGraph `assistant` node, **Claude Desktop's own
model decides when to delegate to the Task Agent** — while presenting the
exact same PingOne identity (apps #2/#3) `backend/` already has. Nothing
downstream needs to know or care: the Task Agent's inbound auth, its own
further RFC 8693 exchange, and `mcp-todos-server`'s policy ACL and OBO
audit log all stay exactly as they are.

**Setup:**

1. Add a second redirect URI to PingOne **app #1** (alongside
   `http://localhost:8000/api/auth/callback`): `http://localhost:8765/callback`.
   `claude-bridge/` does its own one-time browser sign-in (Authorization
   Code + PKCE with a loopback redirect — the same pattern `gh auth
   login`/`ant auth login` use), since it's a local process with no
   browser cookie jar to share with `backend/`'s session.
2. `cd claude-bridge && cp .env.example .env` — fill in the same values
   as `backend/.env`'s `OIDC_*`/`AGENT_*`/`AGENT_DELEGATION_*` (it's
   genuinely the same identity), plus `TASK_AGENT_URL`.
3. Add to Claude Desktop's config
   (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`;
   Windows: `%APPDATA%\Claude\claude_desktop_config.json`):

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

4. Restart Claude Desktop and ask it something todo-related. The first
   call opens a browser for PingOne sign-in; after that, the delegation
   credential is cached in memory until it expires — no more browser
   prompts until you restart the server (Claude Desktop respawns it, so
   that means restarting Claude Desktop, or reloading the connector).

The Task Agent still requires `task-agent/` (and `mcp-todos-server/`)
running — this only replaces `backend/`'s and `frontend/`'s role, not the
rest of the chain. See `CLAUDE.md`'s "Claude Desktop as the orchestrator"
for the full design writeup, including why this is a local stdio server
rather than a remote HTTP connector.

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

## Kubernetes deployment with Docker Hub

The Helm chart in `helm/` deploys five workloads into an existing namespace: the chat frontend, backend/chat agent, task agent, MCP Todos server, and Todos UI. It does not deploy `claude-bridge`, create a namespace, or manage an existing Ping Identity Helm release.

Set your values:

```bash
export DOCKERHUB_USER=your-dockerhub-user
export TAG=v1
export NAMESPACE=your-existing-namespace
export RELEASE=agentorchestration
```

Build and push the Python images from the repository root because they copy `shared/`:

```bash
docker buildx build --platform linux/amd64 -f backend/Dockerfile -t docker.io/$DOCKERHUB_USER/agentorchestration-backend:$TAG . --push
docker buildx build --platform linux/amd64 -f task-agent/Dockerfile -t docker.io/$DOCKERHUB_USER/agentorchestration-task-agent:$TAG . --push
docker buildx build --platform linux/amd64 -f mcp-todos-server/Dockerfile -t docker.io/$DOCKERHUB_USER/agentorchestration-mcp-todos-server:$TAG . --push
```

Build and push the frontend images using their own directories as build contexts:

```bash
docker buildx build --platform linux/amd64 -f frontend/Dockerfile -t docker.io/$DOCKERHUB_USER/agentorchestration-frontend:$TAG frontend --push
docker buildx build --platform linux/amd64 -f mcp-todos-server/frontend/Dockerfile -t docker.io/$DOCKERHUB_USER/agentorchestration-todos-ui:$TAG mcp-todos-server/frontend --push
```

Create an uncommitted values override with your Docker Hub user, tag, two public HTTPS hosts, and Ingress class. The chart defaults include the current five image names and internal Service URL fallbacks. Use separate browser origins such as `https://chat.example.com` and `https://todos.example.com`; register these exact PingOne callbacks:

```text
https://chat.example.com/api/auth/callback
https://todos.example.com/api/auth/callback
```

For production, pre-create or externally manage the backend, task-agent, and MCP server Secrets, then set their `*.secrets.existingSecret` values. Do not commit credentials. OAuth audience values must match the PingOne resources actually configured in your tenant; Kubernetes Service DNS is used for internal HTTP URLs, not automatically as a replacement for PingOne audiences.

Validate and install into the existing namespace:

```bash
helm lint ./helm -f values-pingidentity.yaml
helm template $RELEASE ./helm -n $NAMESPACE -f values-pingidentity.yaml > /tmp/agentorchestration.yaml
kubectl apply --namespace $NAMESPACE --dry-run=server -f /tmp/agentorchestration.yaml
helm upgrade --install $RELEASE ./helm --namespace $NAMESPACE -f values-pingidentity.yaml --wait --timeout 10m
```

Check all five workloads:

```bash
kubectl get deployments,services,pods,ingress -n $NAMESPACE -l app.kubernetes.io/instance=$RELEASE
kubectl rollout status deployment/$RELEASE-backend -n $NAMESPACE
kubectl rollout status deployment/$RELEASE-task-agent -n $NAMESPACE
kubectl rollout status deployment/$RELEASE-mcp-todos-server -n $NAMESPACE
kubectl rollout status deployment/$RELEASE-frontend -n $NAMESPACE
kubectl rollout status deployment/$RELEASE-todos-ui -n $NAMESPACE
```

Smoke-test both public hosts and the task-agent proxy:

```bash
curl -i https://chat.example.com/api/health
curl -i https://chat.example.com/task-agent-api/telemetry
curl -i https://todos.example.com/api/health
```

Keep replicas at one initially because todos, telemetry, audit entries, and token ledgers are in memory.

## Deployed demo URLs

The current Kubernetes deployment exposes two browser origins:

- Chat console: https://rluisi-agent-orchestration-client.ping-devops.com
- Todos UI: https://rluisi-agent-orchestration-todos.ping-devops.com

Useful smoke-test endpoints:

- Chat health: https://rluisi-agent-orchestration-client.ping-devops.com/api/health
- Task Agent telemetry proxy: https://rluisi-agent-orchestration-client.ping-devops.com/task-agent-api/telemetry
- Todos health: https://rluisi-agent-orchestration-todos.ping-devops.com/api/health

These URLs are deployment-specific and require the corresponding PingOne configuration for login. Public browser URLs are different from internal Kubernetes URLs: the backend reaches `agentorchestration-task-agent:9010`, and the Task Agent reaches `agentorchestration-mcp-todos-server:9000/mcp`.

## Release and Git tagging

Use one immutable version tag for all five images and the Helm release; do not use `latest`. Keep per-image overrides such as `backend.imageTag` aligned with the global `image.tag`. After the commit and images are finalized, an annotated Git tag can be created and pushed:

```bash
git tag -a v1.0.0 -m "release v1.0.0"
git push origin v1.0.0
```

Do not commit generated deployment values or credentials. Use `existingSecret`/external secret management for production; any credential-looking values currently present in deployment overrides should be rotated and removed separately.

## How the pieces fit together

- `backend/app/telemetry.py` — the `RecordingSpanProcessor` ring buffer and
  redaction filter every other module wraps calls in via `with_span(...)`.
- `backend/app/auth/oidc.py`, `pkce.py`, `session.py` — PingOne sign-in.
- `backend/app/auth/agent_auth.py` — `client_credentials_grant()`/
  `token_exchange()`, both taking an explicit `client_id`/`client_secret`
  pair now (not a single fixed one off Settings) — the Chat Agent uses a
  *different* PingOne app for step 1 (proving its own identity) than step
  2 (performing Token Exchange).
- `backend/app/auth/routes.py` — wires the above into
  `/api/auth/{login,callback,logout,agent-token,me}`. `/agent-token` takes
  no request body anymore (no more per-scope `{"scope": "..."}`) — it's a
  single, session-wide "approve delegation to the Task Agent" action.
- `backend/app/auth/inbound.py` — the inbound-auth check
  `backend/app/routes/invoke.py` runs on the cached delegation token
  before forwarding it — verified against the *Task Agent's* audience
  (`settings.task_agent_url`), not this service's own, since the token is
  addressed to the Task Agent, not to `backend/` itself.
- `backend/app/agent/graph.py` — the Chat Agent's LangGraph graph:
  `assistant` (Claude) ↔ `tools` (`ask_task_agent_read`/`ask_task_agent_write`),
  streamed over SSE once inbound auth passes for the base session.
- `backend/app/agent/tools.py` — the two delegation tools: real A2A client
  calls to the Task Agent, both forwarding the same single delegation
  token (`config["configurable"]["delegated_token"]`) — the tool the model
  chooses still shapes the request text, not which credential is sent.
- `shared/src/agentorchestration_shared/inbound_auth.py` — the inbound-auth
  verification `backend/`, `task-agent/`, *and* `mcp-todos-server/` all use
  (imported, not duplicated) — signature/issuer/expiry/audience, checked
  fresh every call, plus `email`, `aud`, and `agent_client_id` (the custom
  claim PingOne propagates from an exchange's *actor* token — what every
  policy ACL in this repo actually checks identity against, not the
  top-level `client_id`; see `CLAUDE.md`).
- `task-agent/app/token_grants.py` — this service's own
  `client_credentials_grant()`/`token_exchange()`, deliberately duplicated
  from `backend/app/auth/agent_auth.py`'s identical shape (not shared —
  `shared/` is for inbound-auth verification only).
- `task-agent/app/telemetry.py` — its own `RecordingSpanProcessor` ring
  buffer + redaction filter, the same shape as `backend/app/telemetry.py`
  (also deliberately duplicated, not shared), exposed at `GET /telemetry`.
  Every span from either service carries a `service` field so the
  frontend can tell them apart when a span *name* recurs across processes
  (e.g. `inbound_auth.verify` fires on both sides).
- `task-agent/app/agent_executor.py` — the Task Agent's A2A `AgentExecutor`:
  verifies the incoming delegation token (audience = its own URL, scope
  must contain `agent:delegation`) before touching its own graph
  (`task-agent/app/graph.py`), each step its own span
  (`inbound_auth.verify`, `a2a.task_execute`). Threads
  `identity.agent_client_id` (not `identity.client_id`) into the policy
  check, and the raw verified token itself as `delegation_token` — used
  downstream as the *subject* token for this service's own further
  exchange.
- `task-agent/app/graph.py` — MCP tools fetched once per task for schema
  only (no auth needed for `tools/list`); the real work is in
  `_scoped_tool_call` (wired via `ToolNode`'s `awrap_tool_call` hook):
  policy check, then this service's own Client Credentials (cached per
  task) + a **fresh, per-tool-call** Token Exchange scoped to exactly
  `todos:read` or `todos:write`, then a freshly-authorized MCP connection
  built just for that one call. This is what gives step-up scoping for
  free — nothing about which scope was granted is cached across calls.
  The same file also builds the `judge` node — a `JudgeVerdict` Pydantic
  contract, on `ChatAnthropic` or `ChatGroq` depending on `JUDGE_PROVIDER`
  — that grades `task_assistant`'s proposed answer against the delegated
  request and loops back on `fail`, emitting one `judge.evaluate` span per
  attempt.
- `task-agent/app/policy.py` — identity-ACL plus live PingOne Authorize
  decisions. Before a tool's downstream token exchange, it sends the verified
  delegated token to the deployed Decision Endpoint with
  `evaluateDelegateTasksPolicy=true`; only `PERMIT` allows the call, and a
  denial can carry the policy statement's explanation (for example, that the
  user is not a member of `TodosGroup`). It also requests
  `evaluateEvaluatorOptimizerPolicy=true` once per task; the returned
  `policy-information` statement supplies the judge's total-attempt budget,
  with `JUDGE_MAX_ATTEMPTS` as the local fallback. The inbound token's scope
  remains the generic `agent:delegation`; the MCP server independently checks
  the downstream scope.
- `mcp-todos-server/app/mcp_server.py` — the MCP tool surface itself. Every
  tool independently re-verifies the token task-agent's own exchange
  produced and re-checks policy (`mcp-todos-server/app/policy.py` — this
  one *does* still check scope, since the token genuinely carries it) —
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
  the existing three tools), add an identity ACL entry to
  `mcp-todos-server/app/policy.py` and a required-scope entry to
  `task-agent/app/graph.py`'s `_REQUIRED_SCOPE_SETTING` map (which scope
  task-agent's own exchange requests for it) — each service gates
  independently, neither trusts the other's gate. `MultiServerMCPClient`
  picks up the new tool automatically, nothing else changes.
- **New Chat Agent capability**: add a `@tool` in `backend/app/agent/tools.py`,
  register it in `graph.py`'s `_TOOLS` list. The Chat Agent no longer
  tracks per-capability scopes itself (see "Chained RFC 8693 delegation" in
  `CLAUDE.md`) — it just needs a delegation credential to exist at all,
  which every tool already shares.
- **A third agent**: same shape as `task-agent/` — own `AgentCard`, own
  `AgentExecutor` doing inbound auth first, own graph, and (following the
  pattern task-agent itself now uses) its own PingOne worker-app identity
  plus its own RFC 8693 exchange for whatever resource it actually touches,
  rather than forwarding an upstream token unchanged. `task-agent/app/graph.py`'s
  `_scoped_tool_call` is the worked example to copy.
- **Settings persistence**: the reference app's runtime-editable Settings
  panel (`/api/config` + `/api/settings`) isn't built yet — `app/config.py`
  is structured so adding a writable-at-runtime layer over `Settings` is a
  contained change.
- **Cross-service telemetry for `mcp-todos-server/`**: both the Chat Agent
  and the Task Agent have their own `RecordingSpanProcessor`/`/telemetry`
  endpoint and show up live in the frontend's Telemetry panel and
  architecture diagram, but `mcp-todos-server/` doesn't yet — its own MCP
  tool calls and the RFC 8693 exchange that authorizes them aren't
  instrumented. `task-agent/app/telemetry.py` is the copy-pasteable
  pattern (each service's copy is deliberately independent, not shared —
  give a new copy its own `service.name` and stamp it onto every span; see
  `CLAUDE.md`'s note on why span *names* alone aren't enough to
  disambiguate once a second service reuses one). `mcp-todos-server/`'s
  audit log (`app/audit.py`) is a separate, purpose-built mechanism for a
  different job — attributing OBO actions to a human, not general span
  tracing — not a stand-in for this.
