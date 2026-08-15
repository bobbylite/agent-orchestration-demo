import type { TelemetrySpan } from "../../lib/api"

export type NodeKind = "browser" | "idp" | "agent" | "specialist" | "data"

export interface StoryNodeData {
  [key: string]: unknown
  title: string
  subtitle: string
  kind: NodeKind
  /** Span names (from backend/app/telemetry.py's ring buffer) whose latest
   * instance's attributes get shown on this node — this Chat Agent process
   * is the only one whose spans reach this panel today, see CLAUDE.md
   * "Cross-service telemetry aggregation" — not yet built for task-agent
   * or mcp-todos-server. Nodes for those services show `instrumented`
   * spans as empty and render as "not yet aggregated here" instead of
   * pretending to have live data. */
  spanNames: string[];
}

export interface StoryEdgeMeta {
  /** Span name whose most recent instance drives this edge's "live" state
   * (animated + lit up vs. dim/dashed). Undefined = this hop happens on
   * another service's process and isn't instrumented into this panel yet. */
  spanName?: string
  attributeKeys?: string[]
}

export const STORY_NODES: Array<{
  id: string
  position: { x: number; y: number }
  data: StoryNodeData
}> = [
  {
    id: "browser",
    position: { x: 20, y: 260 },
    data: {
      title: "Browser",
      subtitle: "Signed-in human",
      kind: "browser",
      spanNames: [],
    },
  },
  {
    id: "pingone",
    position: { x: 460, y: 20 },
    data: {
      title: "PingOne",
      subtitle: "OIDC Identity Provider",
      kind: "idp",
      spanNames: ["oidc.login.redirect", "oidc.login.callback", "agent.client_credentials", "agent.token_exchange"],
    },
  },
  {
    id: "chatagent",
    position: { x: 460, y: 300 },
    data: {
      title: "Chat Agent",
      subtitle: "FastAPI + LangGraph (backend/)",
      kind: "agent",
      spanNames: ["agent.invoke", "inbound_auth.verify"],
    },
  },
  {
    id: "taskagent",
    position: { x: 860, y: 300 },
    data: {
      title: "Task Agent",
      subtitle: "Own A2A server (task-agent/)",
      kind: "specialist",
      spanNames: [],
    },
  },
  {
    id: "mcp",
    position: { x: 1260, y: 300 },
    data: {
      title: "MCP Todos Server",
      subtitle: "Tool + data + OBO audit log",
      kind: "data",
      spanNames: [],
    },
  },
  {
    id: "audit",
    position: { x: 1260, y: 540 },
    data: {
      title: "OBO Audit Log",
      subtitle: "mcp-todos-server/app/audit.py",
      kind: "data",
      spanNames: [],
    },
  },
]

export const STORY_EDGES: Array<{
  id: string
  source: string
  target: string
  sourceHandle: string
  targetHandle: string
  label: string
  detail: string
  meta: StoryEdgeMeta
}> = [
  {
    id: "e-login",
    source: "browser",
    target: "pingone",
    sourceHandle: "top",
    targetHandle: "left",
    label: "1. Sign in — Authorization Code + PKCE (S256)",
    detail: "ID token verified against PingOne's JWKS; session sealed into a JWE HttpOnly cookie.",
    meta: { spanName: "oidc.login.callback", attributeKeys: ["identity.sub"] },
  },
  {
    id: "e-session",
    source: "browser",
    target: "chatagent",
    sourceHandle: "bottom",
    targetHandle: "left",
    label: "2. Session cookie",
    detail: "Sufficient on its own for plain chat — never touches a protected resource.",
    meta: {},
  },
  {
    id: "e-approve",
    source: "chatagent",
    target: "pingone",
    sourceHandle: "top",
    targetHandle: "bottom",
    label: "3. Approve action — Client Credentials + Token Exchange (RFC 8693)",
    detail: "Scoped to exactly one capability (e.g. todos:read), requested the first time it's needed.",
    meta: { spanName: "agent.token_exchange", attributeKeys: ["oauth.scope", "identity.agent_client_id"] },
  },
  {
    id: "e-verify-chat",
    source: "chatagent",
    target: "pingone",
    sourceHandle: "top2",
    targetHandle: "bottom2",
    label: "Inbound auth — verify JWT (JWKS), fresh every call",
    detail: "Signature, issuer, expiry, audience — never trusted just because it's in a sealed cookie.",
    meta: { spanName: "inbound_auth.verify", attributeKeys: ["identity.agent_client_id"] },
  },
  {
    id: "e-delegate",
    source: "chatagent",
    target: "taskagent",
    sourceHandle: "right",
    targetHandle: "left",
    label: "4. A2A delegate — forwards the delegated token",
    detail: "Real A2A protocol (Agent Cards, task-based JSON-RPC) — not an in-process call.",
    meta: { spanName: "agent.a2a_delegate", attributeKeys: ["oauth.scope", "a2a.result", "a2a.task_state"] },
  },
  {
    id: "e-verify-task",
    source: "taskagent",
    target: "pingone",
    sourceHandle: "top",
    targetHandle: "right",
    label: "Inbound auth — independently re-verifies the SAME token",
    detail: "task-agent/app/agent_executor.py — no implicit trust in the Chat Agent's say-so.",
    meta: {},
  },
  {
    id: "e-mcp",
    source: "taskagent",
    target: "mcp",
    sourceHandle: "right",
    targetHandle: "left",
    label: "5. MCP tool call — forwards the SAME token one hop further",
    detail: "list_todos / add_todo / complete_todo — rebuilt fresh per task, not a cached connection.",
    meta: {},
  },
  {
    id: "e-verify-mcp",
    source: "mcp",
    target: "pingone",
    sourceHandle: "top",
    targetHandle: "right2",
    label: "Inbound auth — independently re-verifies, a third time",
    detail: "mcp-todos-server/app/mcp_server.py — plus its own policy ACL, not trusting task-agent's gate.",
    meta: {},
  },
  {
    id: "e-audit",
    source: "mcp",
    target: "audit",
    sourceHandle: "bottom",
    targetHandle: "top",
    label: "6. Every call recorded — allowed or denied",
    detail: '"OBO — Agent {name} used {tool} on behalf of {human}" — attributed to the real human, not just the agent.',
    meta: {},
  },
]

export interface HandleSpec {
  id: string
  type: "source" | "target"
  position: "top" | "bottom" | "left" | "right"
  /** CSS offset along the side, so two handles on the same edge of a node
   * (e.g. pingone's two "bottom" targets) don't overlap. */
  offset: string
}

/** Explicit per-node handle layout, keyed by node id — the edges above
 * reference these ids via sourceHandle/targetHandle. Kept as a flat map
 * (rather than derived from STORY_EDGES) so the position of each handle
 * around its node is a deliberate layout choice, not an accident of edge
 * declaration order. */
export const NODE_HANDLES: Record<string, HandleSpec[]> = {
  browser: [
    { id: "top", type: "source", position: "top", offset: "50%" },
    { id: "bottom", type: "source", position: "bottom", offset: "50%" },
  ],
  pingone: [
    { id: "left", type: "target", position: "left", offset: "50%" },
    { id: "bottom", type: "target", position: "bottom", offset: "30%" },
    { id: "bottom2", type: "target", position: "bottom", offset: "70%" },
    { id: "right", type: "target", position: "right", offset: "30%" },
    { id: "right2", type: "target", position: "right", offset: "70%" },
  ],
  chatagent: [
    { id: "left", type: "target", position: "left", offset: "50%" },
    { id: "top", type: "source", position: "top", offset: "30%" },
    { id: "top2", type: "source", position: "top", offset: "70%" },
    { id: "right", type: "source", position: "right", offset: "50%" },
  ],
  taskagent: [
    { id: "left", type: "target", position: "left", offset: "50%" },
    { id: "top", type: "source", position: "top", offset: "50%" },
    { id: "right", type: "source", position: "right", offset: "50%" },
  ],
  mcp: [
    { id: "left", type: "target", position: "left", offset: "50%" },
    { id: "top", type: "source", position: "top", offset: "50%" },
    { id: "bottom", type: "source", position: "bottom", offset: "50%" },
  ],
  audit: [{ id: "top", type: "target", position: "top", offset: "50%" }],
}

/** Most recent span matching one of `names`, or null. Spans arrive newest-last
 * from the API (see TelemetryPanel), so scan from the end. */
export function latestSpan(spans: TelemetrySpan[], names: string[]): TelemetrySpan | null {
  if (names.length === 0) return null
  for (let i = spans.length - 1; i >= 0; i--) {
    if (names.includes(spans[i].name)) return spans[i]
  }
  return null
}

/** A span counts as "live" (drives the pulse/animation) for a short window
 * after it ended, so the diagram visibly reacts to a just-completed action
 * without staying lit forever. */
export const LIVE_WINDOW_MS = 8000

export function isRecent(span: TelemetrySpan | null): boolean {
  if (!span?.end_time) return false
  const endedMsAgo = Date.now() - span.end_time / 1_000_000
  return endedMsAgo >= 0 && endedMsAgo < LIVE_WINDOW_MS
}
