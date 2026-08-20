import { lazy, Suspense, useEffect, useRef, useState } from "react"
import { api, type TelemetrySpan } from "../lib/api"

// Lazy-loaded: React Flow is real weight (~150kB gzipped) that plain chat
// usage should never pay for — only fetched once someone actually opens
// the diagram.
const ArchitectureDiagram = lazy(() =>
  import("./diagram/ArchitectureDiagram").then((m) => ({ default: m.ArchitectureDiagram })),
)
const TokenChainPanel = lazy(() =>
  import("./TokenChainPanel").then((m) => ({ default: m.TokenChainPanel })),
)
const AuthorizeDecisionsPanel = lazy(() =>
  import("./AuthorizeDecisionsPanel").then((m) => ({ default: m.AuthorizeDecisionsPanel })),
)

const POLL_INTERVAL_MS = 2000

export function TelemetryPanel() {
  // Oldest-first / newest-last — matches diagram/flowConfig.ts's latestSpan(),
  // which scans from the end to find the most recent match. Reversed only at
  // render time below, for the panel's own newest-first card list.
  const [spans, setSpans] = useState<TelemetrySpan[]>([])
  const [diagramOpen, setDiagramOpen] = useState(false)
  const [tokensOpen, setTokensOpen] = useState(false)
  const [authorizeOpen, setAuthorizeOpen] = useState(false)
  const [clearing, setClearing] = useState(false)
  const pollGeneration = useRef(0)

  useEffect(() => {
    let cancelled = false
    async function poll() {
      const generation = pollGeneration.current
      // Two independent processes (backend + task-agent), each best-effort:
      // task-agent may not be running in every demo, so a failure fetching
      // its spans shouldn't blank out the Chat Agent's. Merge by start_time
      // rather than concatenating — polls land at different times and each
      // endpoint's own ring buffer is only locally ordered.
      const results = await Promise.allSettled([api.getTelemetry(), api.getTaskAgentTelemetry()])
      const merged = results.flatMap((r) => (r.status === "fulfilled" ? r.value.spans : []))
      merged.sort((a, b) => a.start_time - b.start_time)
      if (!cancelled && generation === pollGeneration.current) setSpans(merged)
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
      <div className="flex shrink-0 flex-col gap-3 border-b border-border px-5 py-3.5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-xs font-semibold tracking-wide text-ink-muted uppercase">OpenTelemetry</h2>
          <span className="rounded-full bg-code-bg px-2 py-0.5 font-mono text-[10px] text-ink-muted">
            {spans.length}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={clearing}
            onClick={async () => {
              pollGeneration.current += 1
              setSpans([])
              setClearing(true)
              await Promise.allSettled([api.clearTelemetry(), api.clearTaskAgentTelemetry()])
              setClearing(false)
            }}
            className="rounded-md border border-border bg-canvas-raised px-2.5 py-1 text-[11px] font-semibold text-ink-muted transition hover:bg-code-bg disabled:opacity-50"
          >
            {clearing ? "Clearing…" : "Clear"}
          </button>
          <button
            type="button"
            onClick={() => setDiagramOpen(true)}
            className="flex items-center gap-1.5 rounded-md border border-brand/30 bg-canvas-raised px-2.5 py-1 text-[11px] font-semibold text-brand transition hover:bg-brand/10"
          >
            <DiagramIcon />
            Diagram
          </button>
          <button
            type="button"
            onClick={() => setTokensOpen(true)}
            className="flex items-center gap-1.5 rounded-md border border-brand/30 bg-canvas-raised px-2.5 py-1 text-[11px] font-semibold text-brand transition hover:bg-brand/10"
          >
            <TokensIcon />
            Tokens
          </button>
          <button
            type="button"
            onClick={() => setAuthorizeOpen(true)}
            className="flex items-center gap-1.5 rounded-md border border-brand/30 bg-canvas-raised px-2.5 py-1 text-[11px] font-semibold text-brand transition hover:bg-brand/10"
          >
            <AuthorizeIcon />
            Decisions
          </button>
        </div>
      </div>

      {diagramOpen && (
        <Suspense fallback={<DiagramLoadingOverlay />}>
          <ArchitectureDiagram spans={spans} onClose={() => setDiagramOpen(false)} />
        </Suspense>
      )}
      {tokensOpen && (
        <Suspense fallback={<DiagramLoadingOverlay />}>
          <TokenChainPanel onClose={() => setTokensOpen(false)} />
        </Suspense>
      )}
      {authorizeOpen && (
        <Suspense fallback={<DiagramLoadingOverlay />}>
          <AuthorizeDecisionsPanel onClose={() => setAuthorizeOpen(false)} />
        </Suspense>
      )}
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
            {spans
              .slice()
              .reverse()
              .map((span) => (
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

function DiagramLoadingOverlay() {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[color-mix(in_srgb,var(--ink)_55%,transparent)] backdrop-blur-sm">
      <div className="rounded-xl border border-border bg-canvas-raised px-5 py-3.5 text-sm text-ink-muted shadow-raised">
        Loading diagram…
      </div>
    </div>
  )
}

function TokensIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="5.5" cy="5.5" r="3.3" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="10.5" cy="10.5" r="3.3" stroke="currentColor" strokeWidth="1.3" />
      <path d="M5.5 2.2v1.1M5.5 7.7v1.1M2.2 5.5h1.1M7.7 5.5h1.1" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
    </svg>
  )
}

function DiagramIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="1" y="2" width="4.5" height="4.5" rx="1" stroke="currentColor" strokeWidth="1.3" />
      <rect x="10.5" y="2" width="4.5" height="4.5" rx="1" stroke="currentColor" strokeWidth="1.3" />
      <rect x="5.75" y="9.5" width="4.5" height="4.5" rx="1" stroke="currentColor" strokeWidth="1.3" />
      <path d="M3.25 6.5V8a1 1 0 0 0 1 1H8m4.75-2.5V8a1 1 0 0 1-1 1H8m0 0v.5" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  )
}

function AuthorizeIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M8 1.5 13.5 3.5V7.5C13.5 11 11.2 13.3 8 14.5C4.8 13.3 2.5 11 2.5 7.5V3.5L8 1.5Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <path d="M5.5 8 7.2 9.7 10.5 6.2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
