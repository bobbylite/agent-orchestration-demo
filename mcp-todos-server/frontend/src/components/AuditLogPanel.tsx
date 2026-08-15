import { useEffect, useState } from "react"
import { api, type AuditEntry } from "../lib/api"

const POLL_INTERVAL_MS = 2000

interface Props {
  signedIn: boolean
}

export function AuditLogPanel({ signedIn }: Props) {
  const [entries, setEntries] = useState<AuditEntry[]>([])

  useEffect(() => {
    if (!signedIn) return
    let cancelled = false
    async function poll() {
      try {
        const { entries: latest } = await api.getAudit()
        if (!cancelled) setEntries(latest)
      } catch {
        // audit is best-effort to display; a failed poll just tries again next tick
      }
    }
    poll()
    const id = setInterval(poll, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [signedIn])

  if (!signedIn) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
        <div className="flex h-11 w-11 items-center justify-center rounded-full bg-code-bg text-ink-muted">
          <ShieldIcon />
        </div>
        <div className="max-w-64">
          <p className="text-sm font-medium text-ink">Sign in to view the audit log</p>
          <p className="mt-1 text-xs text-ink-muted">
            Every direct and on-behalf-of action against this server is recorded here.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-3.5">
        <h2 className="text-xs font-semibold tracking-wide text-ink-muted uppercase">Audit Log</h2>
        <span className="rounded-full bg-code-bg px-2 py-0.5 font-mono text-[10px] text-ink-muted">
          {entries.length}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {entries.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <p className="text-sm text-ink-muted">No activity recorded yet.</p>
            <p className="max-w-56 text-xs text-ink-muted/70">
              Direct actions from this UI and delegated (on-behalf-of) agent calls will appear here as they happen.
            </p>
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {entries.map((entry) => (
              <AuditEntryRow key={entry.id} entry={entry} />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" })
  } catch {
    return iso
  }
}

function AuditEntryRow({ entry }: { entry: AuditEntry }) {
  const isAgent = entry.actor_type === "agent"
  const subject = entry.on_behalf_of_label ?? entry.on_behalf_of_sub ?? "unknown user"
  const agentName = entry.agent_label ?? entry.agent_client_id ?? "unknown agent"

  return (
    <li className="animate-pop-in rounded-xl border border-border bg-canvas-raised p-3.5 text-xs shadow-card">
      <div className="flex items-start justify-between gap-2">
        <p className="text-ink">
          {isAgent ? (
            <>
              <span
                className="mr-1.5 inline-block rounded-md bg-brand/15 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-brand uppercase"
                title="On behalf of — a delegated agent action, attributed to the human it was authorized for"
              >
                OBO
              </span>
              Agent <strong className="font-semibold">{agentName}</strong> used{" "}
              <span className="font-mono text-[11px]">{entry.tool}</span> on behalf of{" "}
              <strong className="font-semibold">{subject}</strong>
            </>
          ) : (
            <>
              <strong className="font-semibold">{subject}</strong> used{" "}
              <span className="font-mono text-[11px]">{entry.tool}</span> directly
            </>
          )}
        </p>
        <OutcomeBadge outcome={entry.outcome} />
      </div>

      <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 border-t border-border pt-2.5 font-mono text-[11px]">
        <div className="contents">
          <dt className="whitespace-nowrap text-ink-muted">time</dt>
          <dd className="min-w-0 break-all text-ink">{formatTime(entry.timestamp)}</dd>
        </div>
        {isAgent && entry.agent_aud && (
          <div className="contents">
            <dt className="whitespace-nowrap text-ink-muted">aud</dt>
            <dd className="min-w-0 break-all text-ink">{entry.agent_aud}</dd>
          </div>
        )}
        {isAgent && entry.agent_client_id && (
          <div className="contents">
            <dt className="whitespace-nowrap text-ink-muted">client_id</dt>
            <dd className="min-w-0 break-all text-ink">{entry.agent_client_id}</dd>
          </div>
        )}
        {isAgent && entry.scope && (
          <div className="contents">
            <dt className="whitespace-nowrap text-ink-muted">scope</dt>
            <dd className="min-w-0 break-all text-ink">{entry.scope}</dd>
          </div>
        )}
        <div className="contents">
          <dt className="whitespace-nowrap text-ink-muted">sub</dt>
          <dd className="min-w-0 break-all text-ink">{entry.on_behalf_of_sub ?? "—"}</dd>
        </div>
        {entry.detail && (
          <div className="contents">
            <dt className="whitespace-nowrap text-ink-muted">detail</dt>
            <dd className="min-w-0 break-all text-ink">{entry.detail}</dd>
          </div>
        )}
      </dl>
    </li>
  )
}

function OutcomeBadge({ outcome }: { outcome: AuditEntry["outcome"] }) {
  const isSuccess = outcome === "success"
  return (
    <span
      className={`shrink-0 rounded-md px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase ${
        isSuccess ? "bg-success/10 text-success" : "bg-danger-bg text-danger"
      }`}
    >
      {outcome}
    </span>
  )
}

function ShieldIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path
        d="M10 2.5 16.5 5v5c0 4-2.8 6.9-6.5 8-3.7-1.1-6.5-4-6.5-8V5L10 2.5Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M7.3 10 9.2 11.9 12.9 8.2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
