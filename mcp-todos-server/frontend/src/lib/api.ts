export interface MeResponse {
  oidc_enabled: boolean
  signed_in: boolean
  sub: string | null
  email: string | null
  name: string | null
}

export type CreatedBy = "human" | "agent"

export interface Todo {
  id: string
  text: string
  done: boolean
  created_at: string
  created_by: CreatedBy
  creator_sub: string | null
  creator_label: string | null
  agent_client_id: string | null
}

export type AuditActorType = "human" | "agent"
export type AuditOutcome = "success" | "denied" | "error"

export interface AuditEntry {
  id: string
  timestamp: string
  actor_type: AuditActorType
  tool: string
  outcome: AuditOutcome
  on_behalf_of_sub: string | null
  on_behalf_of_label: string | null
  agent_client_id: string | null
  agent_aud: string | null
  agent_label: string | null
  scope: string | null
  detail: string | null
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
  getMe: () => getJson<MeResponse>("/api/auth/me"),
  getTodos: () => getJson<{ todos: Todo[] }>("/api/todos"),
  addTodo: (text: string) =>
    getJson<Todo>("/api/todos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),
  completeTodo: (id: string) => getJson<Todo>(`/api/todos/${id}/complete`, { method: "POST" }),
  getAudit: () => getJson<{ entries: AuditEntry[] }>("/api/audit"),
  loginUrl: "/api/auth/login",
  logoutUrl: "/api/auth/logout",
}
