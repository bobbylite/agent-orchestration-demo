import { useState } from "react"
import { api, type MeResponse } from "../lib/api"

type Status = "idle" | "loading" | "success" | "error"

interface Props {
  me: MeResponse | null
  onAuthenticated: () => void
}

export function AgentAuthButton({ me, onAuthenticated }: Props) {
  const [status, setStatus] = useState<Status>("idle")
  const [error, setError] = useState<string | null>(null)

  if (!me?.agent_enabled) {
    return (
      <span className="text-xs text-ink-muted" title="Set AGENT_CLIENT_ID / AGENT_CLIENT_SECRET / SESSION_SECRET">
        Agent authentication not configured
      </span>
    )
  }

  const disabled = !me.signed_in || status === "loading"
  const authenticated = me.agent_authenticated && me.exchanged
  const title = !me.signed_in ? "Sign in with PingOne first" : status === "error" ? (error ?? undefined) : undefined

  async function handleClick() {
    setStatus("loading")
    setError(null)
    try {
      await api.authenticateAgent()
      setStatus("success")
      onAuthenticated()
    } catch (err) {
      setStatus("error")
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      title={title}
      className="flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-medium text-ink transition hover:border-brand disabled:cursor-not-allowed disabled:opacity-40"
    >
      {status === "loading" ? (
        <>
          <Spinner /> Authenticating…
        </>
      ) : authenticated ? (
        <>
          <span className="text-success">●</span> Agent Authenticated
        </>
      ) : status === "error" ? (
        <span className="text-danger">Authentication failed — retry</span>
      ) : (
        "Authenticate Agent"
      )}
    </button>
  )
}

function Spinner() {
  return (
    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-ink-muted border-t-transparent" />
  )
}
