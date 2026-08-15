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
  agent_authenticated: boolean
  exchanged: boolean
}

export interface TelemetrySpan {
  name: string
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
  authenticateAgent: () =>
    getJson<{ agent_authenticated: boolean; exchanged: boolean }>("/api/auth/agent-token", { method: "POST" }),
  loginUrl: "/api/auth/login",
  logoutUrl: "/api/auth/logout",
}

export interface StreamHandlers {
  onToken: (text: string) => void
  onDone: () => void
  onError: (message: string) => void
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
    buffer += decoder.decode(value, { stream: true })

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
    }
  }
}
