import { useEffect, useState } from "react"
import { api, type TelemetrySpan } from "../lib/api"

const POLL_INTERVAL_MS = 2000

export function TelemetryPanel() {
  const [spans, setSpans] = useState<TelemetrySpan[]>([])

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const { spans: latest } = await api.getTelemetry()
        if (!cancelled) setSpans(latest.slice().reverse())
      } catch {
        // telemetry is best-effort; a failed poll just tries again next tick
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
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-3.5">
        <h2 className="text-xs font-semibold tracking-wide text-ink-muted uppercase">OpenTelemetry Spans</h2>
        <span className="rounded-full bg-code-bg px-2 py-0.5 font-mono text-[10px] text-ink-muted">
          {spans.length}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {spans.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <p className="text-sm text-ink-muted">No spans recorded yet.</p>
            <p className="max-w-52 text-xs text-ink-muted/70">
              Sign in and authenticate the agent to see identity and delegation events appear here.
            </p>
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {spans.map((span) => (
              <li
                key={span.span_id}
                className="animate-pop-in rounded-xl border border-border bg-canvas-raised p-3.5 text-xs shadow-card"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[13px] font-semibold text-ink">{span.name}</span>
                  <StatusBadge status={span.status} />
                </div>
                <div className="mt-1.5 flex items-center gap-2 text-[11px] text-ink-muted">
                  {span.parent_span_id && (
                    <span className="rounded bg-code-bg px-1.5 py-0.5 font-mono" title="has parent span">
                      ↳ nested
                    </span>
                  )}
                  {span.duration_ms != null && <span className="font-mono">{span.duration_ms.toFixed(1)}ms</span>}
                </div>
                {Object.keys(span.attributes).length > 0 && (
                  <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 border-t border-border pt-2.5 font-mono text-[11px]">
                    {Object.entries(span.attributes).map(([key, value]) => (
                      <div key={key} className="contents">
                        <dt className="whitespace-nowrap text-ink-muted">{key}</dt>
                        <dd className="min-w-0 break-all text-ink">{String(value)}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const isError = status === "ERROR"
  return (
    <span
      className={`shrink-0 rounded-md px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase ${
        isError ? "bg-danger-bg text-danger" : "bg-success/10 text-success"
      }`}
    >
      {status}
    </span>
  )
}
