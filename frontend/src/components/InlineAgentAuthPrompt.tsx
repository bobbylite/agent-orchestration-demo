import { useState } from "react"

type Status = "idle" | "loading" | "error"

interface Props {
  onAuthenticate: () => Promise<void>
}

export function InlineAgentAuthPrompt({ onAuthenticate }: Props) {
  const [status, setStatus] = useState<Status>("idle")
  const [error, setError] = useState<string | null>(null)

  async function handleClick() {
    setStatus("loading")
    setError(null)
    try {
      await onAuthenticate()
      // On success the parent removes this prompt (it re-runs the turn),
      // so no "success" status is needed here.
    } catch (err) {
      setStatus("error")
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="mt-3 flex items-start gap-3 rounded-lg border border-brand/25 bg-brand/5 px-3.5 py-3">
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand/15 text-brand">
        <LockIcon />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-xs text-ink">This needs the agent to act on your behalf — it isn't authenticated yet.</p>
        {status === "error" && error && <p className="mt-1 text-xs text-danger">{error}</p>}
      </div>
      <button
        type="button"
        onClick={handleClick}
        disabled={status === "loading"}
        className="shrink-0 rounded-md border border-brand/30 bg-canvas-raised px-3 py-1.5 text-xs font-semibold text-brand transition hover:bg-brand/10 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {status === "loading" ? "Authenticating…" : "Authenticate Agent"}
      </button>
    </div>
  )
}

function LockIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <rect x="2.5" y="6" width="9" height="6" rx="1.2" stroke="currentColor" strokeWidth="1.3" />
      <path d="M4.5 6V4.25A2.5 2.5 0 0 1 9.5 4.25V6" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  )
}
