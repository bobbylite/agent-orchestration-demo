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
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">OpenTelemetry Spans</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {spans.length === 0 ? (
          <p className="text-xs text-ink-muted">No spans recorded yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {spans.map((span) => (
              <li key={span.span_id} className="rounded-lg border border-border bg-canvas-raised p-2.5 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono font-medium text-ink">{span.name}</span>
                  <StatusBadge status={span.status} />
                </div>
                <div className="mt-1 flex items-center gap-2 text-ink-muted">
                  {span.parent_span_id && <span title="has parent span">↳ nested</span>}
                  {span.duration_ms != null && <span>{span.duration_ms.toFixed(1)}ms</span>}
                </div>
                {Object.keys(span.attributes).length > 0 && (
                  <dl className="mt-1.5 flex flex-col gap-0.5 font-mono text-[11px] text-ink-muted">
                    {Object.entries(span.attributes).map(([key, value]) => (
                      <div key={key} className="flex gap-1">
                        <dt className="shrink-0">{key}=</dt>
                        <dd className="truncate text-ink">{String(value)}</dd>
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
      className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase ${
        isError ? "bg-danger-bg text-danger" : "bg-code-bg text-ink-muted"
      }`}
    >
      {status}
    </span>
  )
}
