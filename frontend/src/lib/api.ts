export interface ConfigResponse {
  oidc_enabled: boolean
  agent_enabled: boolean
  agent_model: string
}

export interface MeResponse {
  oidc_enabled: boolean
  agent_enabled: boolean
  signed_in: boolean
  sub: string | null
  email: string | null
  name: string | null
  /** Whether the agent already holds a delegation credential for the Task
   * Agent this session (Client Credentials + RFC 8693 Token Exchange) — one
   * generic credential, not one per action; see CLAUDE.md 2026-08-16. */
  agent_delegated: boolean
}

export interface TokenLedgerEntry {
  /** Raw compact JWT — only ever fetched when the Token Chain panel is
   * open, and only rendered behind an explicit "reveal" toggle client-side. */
  raw: string
  claims: Record<string, unknown>
  /** Only present on task-agent's mcp_scoped_read/mcp_scoped_write slots —
   * which MCP tool call last produced this exact token. */
  tool?: string
}

export interface TokenChainResponse {
  user: TokenLedgerEntry | null
  agent_own: TokenLedgerEntry | null
  delegation: TokenLedgerEntry | null
}

export interface TaskAgentTokenChainResponse {
  task_agent_own: TokenLedgerEntry | null
  mcp_scoped_read: TokenLedgerEntry | null
  mcp_scoped_write: TokenLedgerEntry | null
}

export interface TelemetrySpan {
  name: string
  /** OTel resource `service.name` — disambiguates spans that share a name
   * across services (e.g. "inbound_auth.verify" is emitted by both backend/
   * and task-agent/) now that TelemetryPanel merges both services' spans
   * into one array. See diagram/flowConfig.ts's SERVICE_* constants. */
  service: string
  trace_id: string
  span_id: string
  parent_span_id: string | null
  start_time: number
  end_time: number | null
  duration_ms: number | null
  status: string
  attributes: Record<string, unknown>
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: "include", ...init })
  if (!res.ok) {
    const body = await res.text().catch(() => "")
    throw new Error(`${res.status} ${res.statusText}${body ? `: ${body}` : ""}`)
  }
  return (await res.json()) as T
}

export const api = {
  getConfig: () => getJson<ConfigResponse>("/api/config"),
  getMe: () => getJson<MeResponse>("/api/auth/me"),
  getTelemetry: () => getJson<{ spans: TelemetrySpan[] }>("/api/telemetry"),
  /** task-agent's own spans (inbound_auth.verify, a2a.task_execute,
   * judge.evaluate — see task-agent/app/telemetry.py) — a separate process
   * from the Chat Agent backend, proxied via vite (see vite.config.ts)
   * rather than merged server-side. Best-effort: task-agent may not be
   * running in every demo, so callers should treat a failure here as "no
   * data yet", not a hard error. */
  getTaskAgentTelemetry: () => getJson<{ spans: TelemetrySpan[] }>("/task-agent-api/telemetry"),
  /** This service's half of the real token chain — decoded (+ raw) claims
   * for the user's own token, the orchestration agent's own Client
   * Credentials token, and the delegation token their Token Exchange
   * produced. Null slots mean that step hasn't actually happened yet in
   * this process's lifetime (e.g. "Approve Agent Action" was never
   * clicked) — this endpoint only reports real history, it never mints
   * anything on its own. */
  getTokenChain: () => getJson<TokenChainResponse>("/api/auth/token-chain"),
  /** task-agent's half of the chain — its own Client Credentials token
   * plus whichever MCP-scoped (todos:read / todos:write) tokens its last
   * real tool calls actually obtained. Same "reports reality, never
   * synthesizes" contract as getTokenChain above. */
  getTaskAgentTokenChain: () => getJson<TaskAgentTokenChainResponse>("/task-agent-api/tokens/chain"),
  /** Approve delegating to the Task Agent — Client Credentials + RFC 8693
   * Token Exchange, scoped generically (not per todos:read/write action;
   * the Task Agent decides that itself once it holds this credential). */
  approveAgentAction: () =>
    getJson<{ delegated: boolean }>("/api/auth/agent-token", { method: "POST" }),
  loginUrl: "/api/auth/login",
  logoutUrl: "/api/auth/logout",
}

export interface StreamHandlers {
  onToken: (text: string) => void
  onDone: () => void
  onError: (message: string) => void
  /** The agent tried to act on the user's behalf without a delegation
   * credential yet. Not an error — the turn continues normally and the
   * model explains it in its own words; this is the deterministic signal
   * for rendering an inline "Approve Agent Action" prompt. */
  onAuthRequired?: () => void
  onRawEvent?: (event: string, data: string) => void
}

export async function streamInvoke(
  threadId: string,
  message: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/invoke", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId, message }),
    signal,
  })

  if (!res.ok || !res.body) {
    const body = await res.text().catch(() => "")
    handlers.onError(`${res.status} ${res.statusText}${body ? `: ${body}` : ""}`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    // sse-starlette frames events with CRLF; normalize before splitting so
    // "\n\n" reliably matches the blank line between events.
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n")

    let sepIndex: number
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, sepIndex)
      buffer = buffer.slice(sepIndex + 2)

      let eventName = "message"
      let data = ""
      for (const line of rawEvent.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim()
        else if (line.startsWith("data:")) data += line.slice(5).trim()
      }
      if (!data) continue

      handlers.onRawEvent?.(eventName, data)
      const parsed = JSON.parse(data)
      if (eventName === "token") handlers.onToken(parsed.text)
      else if (eventName === "done") handlers.onDone()
      else if (eventName === "error") handlers.onError(parsed.message)
      else if (eventName === "auth_required") handlers.onAuthRequired?.()
    }
  }
}
