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
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled}
        className="flex items-center gap-2 rounded-full border border-border px-4 py-2 text-sm font-medium text-ink transition hover:border-brand disabled:cursor-not-allowed disabled:opacity-40"
      >
        {status === "loading" ? (
          <>
            <Spinner /> Authenticating…
          </>
        ) : authenticated ? (
          <>
            <span className="text-success">●</span> Agent Authenticated
          </>
        ) : (
          "Authenticate Agent"
        )}
      </button>
      {!me.signed_in && <span className="text-xs text-ink-muted">Sign in first</span>}
      {status === "error" && error && <span className="max-w-64 text-right text-xs text-danger">{error}</span>}
    </div>
  )
}

function Spinner() {
  return (
    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-ink-muted border-t-transparent" />
  )
}
