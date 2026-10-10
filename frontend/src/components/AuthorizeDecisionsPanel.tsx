import { useEffect, useRef, useState } from "react"
import { api, type AuthorizeDecisionEntry } from "../lib/api"

interface Props {
  onClose: () => void
}

const POLL_INTERVAL_MS = 2000

const POLICY_LABELS: Record<AuthorizeDecisionEntry["policy"], string> = {
  evaluator_optimizer: "Judge Retry Budget",
  task_policy: "Task Policy",
  delegation_policy: "Delegation Policy",
}

export function AuthorizeDecisionsPanel({ onClose }: Props) {
  const [entries, setEntries] = useState<AuthorizeDecisionEntry[]>([])
  const [clearing, setClearing] = useState(false)
  const pollGeneration = useRef(0)

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  useEffect(() => {
    let cancelled = false
    async function poll() {
      const generation = pollGeneration.current
      // task-agent only — it's the only service that calls PingOne
      // Authorize's Decision Endpoint (see task-agent/app/policy.py). A
      // failed fetch (task-agent not running) just leaves the list as-is,
      // same best-effort pattern as TelemetryPanel/TokenChainPanel.
      try {
        const res = await api.getAuthorizeDecisions()
        if (!cancelled && generation === pollGeneration.current) setEntries(res.entries)
      } catch {
        // ignore — degrade to whatever was last successfully fetched
      }
    }
    poll()
    const id = setInterval(poll, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[color-mix(in_srgb,var(--ink)_55%,transparent)] p-2 backdrop-blur-sm sm:p-6">
      <div role="dialog" aria-modal="true" aria-labelledby="authorize-decisions-title" className="flex max-h-[calc(100dvh-1rem)] h-full w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border bg-canvas shadow-floating sm:rounded-2xl">
        <div
          className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-3 py-3 sm:flex-nowrap sm:px-6 sm:py-4"
          style={{ backgroundImage: "linear-gradient(120deg, color-mix(in srgb, var(--brand) 10%, transparent), transparent 60%)" }}
        >
          <div>
            <h2 id="authorize-decisions-title" className="text-sm font-semibold tracking-tight text-ink">PingOne Authorize — decision history</h2>
            <p className="text-xs text-ink-muted">
              Every decision-endpoint call task-agent has made — which policy, which agent, PERMIT or DENY, and why.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-code-bg px-2 py-0.5 font-mono text-[10px] text-ink-muted">{entries.length}</span>
            <button
              type="button"
              disabled={clearing}
              onClick={async () => {
                pollGeneration.current += 1
                setEntries([])
                setClearing(true)
                await api.clearAuthorizeDecisions().catch(() => null)
                setClearing(false)
              }}
              className="rounded-md border border-border bg-canvas-raised px-2.5 py-1 text-[11px] font-semibold text-ink-muted transition hover:bg-code-bg disabled:opacity-50"
            >
              {clearing ? "Clearing…" : "Clear"}
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close authorize decisions"
              className="touch-target flex h-11 w-11 shrink-0 items-center justify-center sm:h-8 sm:w-8 rounded-full text-ink-muted transition hover:bg-code-bg hover:text-ink"
            >
              <CloseIcon />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-4 sm:px-6 sm:py-5">
          {entries.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
              <p className="text-sm text-ink-muted">No Authorize decisions recorded yet.</p>
              <p className="max-w-64 text-xs text-ink-muted/70">
                Ask the assistant something that needs the Task Agent (e.g. a todo list question) to see decisions
                appear here.
              </p>
            </div>
          ) : (
            <ul className="mx-auto flex max-w-lg flex-col gap-2.5">
              {entries.map((entry) => (
                <DecisionRow key={entry.id} entry={entry} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}

function DecisionRow({ entry }: { entry: AuthorizeDecisionEntry }) {
  return (
    <li className="animate-pop-in overflow-hidden rounded-xl border border-border bg-canvas-raised shadow-card">
      <div className="flex items-start justify-between gap-2 px-4 py-3.5">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-[13px] font-semibold text-ink">{POLICY_LABELS[entry.policy]}</span>
            {entry.tool && (
              <span className="rounded-md bg-code-bg px-1.5 py-0.5 font-mono text-[10px] text-ink-muted">{entry.tool}</span>
            )}
          </div>
          <p className="mt-0.5 truncate text-[10.5px] text-ink-muted">
            {entry.agent_client_id ? `agent ${entry.agent_client_id}` : "agent identity unknown"} ·{" "}
            {new Date(entry.timestamp).toLocaleTimeString()}
          </p>
          {entry.reason && <p className="mt-1 text-[10.5px] leading-snug text-ink-muted/80">{entry.reason}</p>}
        </div>
        <DecisionBadge decision={entry.decision} />
      </div>
    </li>
  )
}

function DecisionBadge({ decision }: { decision: AuthorizeDecisionEntry["decision"] }) {
  const styles: Record<AuthorizeDecisionEntry["decision"], string> = {
    permit: "bg-success/10 text-success",
    deny: "bg-danger-bg text-danger",
    error: "bg-warning/10 text-warning",
  }
  const labels: Record<AuthorizeDecisionEntry["decision"], string> = {
    permit: "PERMIT",
    deny: "DENY",
    error: "ERROR",
  }
  return (
    <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[9.5px] font-semibold ${styles[decision]}`}>
      {labels[decision]}
    </span>
  )
}

function CloseIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3 3L13 13M13 3L3 13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}
