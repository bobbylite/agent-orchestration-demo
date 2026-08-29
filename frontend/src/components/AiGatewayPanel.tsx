import { useEffect, useRef, useState } from "react"
import { api, type GatewayActivity, type GatewayInsights } from "../lib/api"

interface Props { onClose: () => void }
const POLL_INTERVAL_MS = 2000

export function AiGatewayPanel({ onClose }: Props) {
  const [insights, setInsights] = useState<GatewayInsights | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)
  const generation = useRef(0)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose()
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  useEffect(() => {
    let cancelled = false
    async function poll() {
      const current = generation.current
      try {
        const value = await api.getGatewayInsights()
        if (!cancelled && current === generation.current) { setInsights(value); setError(null) }
      } catch (cause) {
        if (!cancelled && current === generation.current) setError(cause instanceof Error ? cause.message : "Gateway insights unavailable")
      }
    }
    poll()
    const id = setInterval(poll, POLL_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  const status = error ? "UNAVAILABLE" : insights?.summary.available_tools ? "CONNECTED" : "AUTH REQUIRED"

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[color-mix(in_srgb,var(--ink)_55%,transparent)] p-2 backdrop-blur-sm sm:p-6">
      <div role="dialog" aria-modal="true" aria-labelledby="gateway-title" className="flex max-h-[calc(100dvh-1rem)] h-full w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-border bg-canvas shadow-floating sm:rounded-2xl">
        <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-3 py-3 sm:px-6 sm:py-4" style={{ backgroundImage: "linear-gradient(120deg, color-mix(in srgb, var(--warning) 13%, transparent), transparent 60%)" }}>
          <div>
            <h2 id="gateway-title" className="text-sm font-semibold tracking-tight text-ink">PingOne Privilege AI Gateway</h2>
            <p className="text-xs text-ink-muted">Live tool inventory, gateway calls, and policy outcomes.</p>
          </div>
          <div className="flex items-center gap-2">
            <span className={`rounded-md px-2 py-0.5 text-[10px] font-semibold ${status === "CONNECTED" ? "bg-success/10 text-success" : status === "UNAVAILABLE" ? "bg-danger-bg text-danger" : "bg-warning/10 text-warning"}`}>{status}</span>
            <button type="button" disabled={clearing} onClick={async () => { generation.current += 1; setInsights(null); setClearing(true); await api.clearGatewayInsights().catch(() => null); setClearing(false) }} className="rounded-md border border-border bg-canvas-raised px-2.5 py-1 text-[11px] font-semibold text-ink-muted transition hover:bg-code-bg disabled:opacity-50">{clearing ? "Clearing…" : "Clear"}</button>
            <button type="button" onClick={onClose} aria-label="Close AI Gateway" className="touch-target flex h-11 w-11 items-center justify-center rounded-full text-ink-muted transition hover:bg-code-bg hover:text-ink sm:h-8 sm:w-8"><CloseIcon /></button>
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-4 sm:px-6 sm:py-5">
          {error ? <EmptyState title="Gateway insights unavailable" copy={error} /> : !insights ? <EmptyState title="Connecting to gateway observer…" copy="Waiting for the Task Agent's bounded activity surface." /> : <>
            <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">{[["Tools", insights.summary.available_tools], ["Calls", insights.summary.calls], ["Blocked", insights.summary.denied], ["Errors", insights.summary.errors]].map(([label, value]) => <div key={label} className="rounded-xl border border-border bg-canvas-raised p-3 shadow-card"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">{label}</p><p className="mt-1 text-xl font-semibold text-ink">{value}</p></div>)}</div>
            {insights.summary.available_tools === 0 && <div className="mb-5 rounded-xl border border-warning/40 bg-warning/10 p-4 text-xs text-ink"><strong>No tools are currently available.</strong><p className="mt-1 text-ink-muted">The gateway may require authentication, or its policy may not expose any tools to this client.</p>{insights.last_discovery?.reason && <p className="mt-2 font-mono text-[10px] text-warning">{insights.last_discovery.reason}</p>}<a href={api.gatewayLoginUrl} className="mt-3 inline-block font-semibold text-brand underline">Authenticate AI Gateway</a></div>}
            <section><h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">Tools available</h3><div className="grid gap-2 sm:grid-cols-2">{insights.tools.map((tool) => <article key={tool.name} className="rounded-xl border border-border bg-canvas-raised p-3 shadow-card"><div className="flex items-center justify-between gap-2"><span className="font-mono text-[12px] font-semibold text-ink">{tool.name}</span><span className="rounded-md bg-success/10 px-1.5 py-0.5 text-[9px] font-semibold text-success">AVAILABLE</span></div><p className="mt-1 text-[10.5px] leading-snug text-ink-muted">{tool.description || "No description provided."}</p><pre className="mt-2 max-h-20 overflow-auto rounded-md bg-code-bg p-2 text-[9px] text-ink-muted">{JSON.stringify(tool.input_schema, null, 2)}</pre></article>)}</div></section>
            <section className="mt-6"><h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">Gateway activity</h3><div className="flex flex-col gap-2">{insights.activities.length ? insights.activities.map((entry) => <ActivityRow key={entry.id} entry={entry} />) : <p className="rounded-xl border border-border p-4 text-xs text-ink-muted">No gateway calls recorded yet. Ask for a todo list to begin.</p>}</div></section>
          </>}
        </div>
      </div>
    </div>
  )
}

function ActivityRow({ entry }: { entry: GatewayActivity }) {
  const denied = entry.outcome === "policy_denied"
  return <article className={`animate-pop-in rounded-xl border p-3 shadow-card ${denied ? "border-danger/50 bg-danger-bg" : "border-border bg-canvas-raised"}`}><div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><span className="font-mono text-[11px] font-semibold text-ink">{entry.operation === "schema_discovery" ? "TOOLS LIST" : entry.tool || "TOOL CALL"}</span>{denied && <span className="rounded-md bg-danger/15 px-1.5 py-0.5 text-[9px] font-bold text-danger">POLICY BLOCKED</span>}</div><p className="mt-1 text-[10px] text-ink-muted">{new Date(entry.timestamp).toLocaleTimeString()} {entry.http_status ? `· HTTP ${entry.http_status}` : ""}</p></div><span className={`rounded-md px-1.5 py-0.5 text-[9px] font-semibold ${entry.outcome === "ok" ? "bg-success/10 text-success" : denied ? "bg-danger/15 text-danger" : "bg-warning/10 text-warning"}`}>{entry.outcome.replace("_", " ").toUpperCase()}</span></div>{denied && <p className="mt-2 text-[10.5px] font-semibold text-danger">The gateway accepted the connection but its policy did not permit this tool.</p>}{entry.reason && <p className="mt-1 font-mono text-[10px] text-ink-muted">{entry.reason}</p>}</article>
}

function EmptyState({ title, copy }: { title: string; copy: string }) { return <div className="flex h-full min-h-48 flex-col items-center justify-center gap-2 text-center"><p className="text-sm text-ink-muted">{title}</p><p className="max-w-md text-xs text-ink-muted/70">{copy}</p></div> }
function CloseIcon() { return <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 3L13 13M13 3L3 13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg> }
