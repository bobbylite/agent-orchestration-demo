import type { CSSProperties, ReactElement } from "react"
import { Handle, Position, type NodeProps } from "@xyflow/react"
import { isRecent, latestSpan, NODE_HANDLES, type StoryNodeData } from "./flowConfig"
import type { TelemetrySpan } from "../../lib/api"

const POSITION_MAP = {
  top: Position.Top,
  bottom: Position.Bottom,
  left: Position.Left,
  right: Position.Right,
} as const

const KIND_STYLES: Record<StoryNodeData["kind"], { accent: string; icon: ReactElement }> = {
  browser: { accent: "var(--ink-muted)", icon: <BrowserIcon /> },
  idp: { accent: "var(--brand)", icon: <ShieldIcon /> },
  agent: { accent: "var(--brand)", icon: <SparkIcon /> },
  specialist: { accent: "var(--success)", icon: <WrenchIcon /> },
  data: { accent: "var(--warning)", icon: <DbIcon /> },
  // A blend of the agent and specialist accents — the judge lives inside
  // task-agent's own process (no separate identity, no PingOne hop of its
  // own; see the e-judge-propose/e-judge-retry edges) so it's visually
  // "between" those two kinds rather than a fresh color.
  judge: { accent: "color-mix(in srgb, var(--brand) 55%, var(--success) 45%)", icon: <ScaleIcon /> },
  // Same muted tone as "browser" — Claude Desktop is the same signed-in
  // human, just a second front door, not a new trust tier.
  desktop: { accent: "var(--ink-muted)", icon: <DesktopIcon /> },
}

interface Props extends NodeProps {
  data: StoryNodeData
  spans: TelemetrySpan[]
}

export function StoryNode({ id, data, spans }: Props) {
  const style = KIND_STYLES[data.kind]
  const handles = NODE_HANDLES[id] ?? []
  const instrumented = data.spanNames.length > 0
  const match = instrumented ? latestSpan(spans, data.spanNames, data.service) : null
  const active = isRecent(match)

  // `--node-glow-color` feeds the `diagram-node-glow` keyframe in index.css
  // (a breathing box-shadow) — the accent varies per node kind, the
  // animation itself doesn't, so the color rides in as a custom property
  // rather than being baked into the keyframe.
  const nodeStyle: CSSProperties = active
    ? ({ borderColor: style.accent, "--node-glow-color": style.accent } as CSSProperties)
    : { borderColor: "var(--border)" }

  return (
    <div
      className={`w-56 rounded-xl border bg-canvas-raised px-4 py-3.5 shadow-raised transition-all ${active ? "diagram-node-glow" : ""}`}
      style={nodeStyle}
    >
      {handles.map((h) => (
        <Handle
          key={h.id}
          id={h.id}
          type={h.type}
          position={POSITION_MAP[h.position]}
          style={{ [h.position === "top" || h.position === "bottom" ? "left" : "top"]: h.offset, background: "var(--border)", width: 6, height: 6, border: "none" }}
        />
      ))}

      <div className="flex items-center gap-2.5">
        <div
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
          style={{ backgroundColor: `color-mix(in srgb, ${style.accent} 16%, transparent)`, color: style.accent }}
        >
          {style.icon}
        </div>
        <div className="min-w-0">
          <div className="truncate text-[13px] font-semibold text-ink">{data.title}</div>
          <div className="truncate text-[10.5px] text-ink-muted">{data.subtitle}</div>
        </div>
        {instrumented && (
          <span
            className="ml-auto h-2 w-2 shrink-0 rounded-full"
            style={{ backgroundColor: active ? "var(--success)" : "var(--border)" }}
            title={active ? "Recently active" : "No recent activity"}
          />
        )}
      </div>

      {instrumented ? (
        match ? (
          <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 border-t border-border pt-2 font-mono text-[10px]">
            {Object.entries(match.attributes)
              .slice(0, 3)
              .map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="whitespace-nowrap text-ink-muted">{k.split(".").pop()}</dt>
                  <dd className="min-w-0 truncate text-ink">{String(v)}</dd>
                </div>
              ))}
          </dl>
        ) : (
          <p className="mt-2.5 border-t border-border pt-2 text-[10px] text-ink-muted/70">Waiting for activity…</p>
        )
      ) : (
        <p className="mt-2.5 border-t border-border pt-2 text-[10px] text-ink-muted/70">
          Own process — not aggregated into this panel yet
        </p>
      )}
    </div>
  )
}

function BrowserIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="1.5" y="2.5" width="13" height="11" rx="1.5" stroke="currentColor" strokeWidth="1.3" />
      <path d="M1.5 5.5H14.5" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  )
}
function ShieldIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M8 1.8 13.5 4v4c0 3.4-2.3 5.7-5.5 6.6C4.8 13.7 2.5 11.4 2.5 8V4L8 1.8Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  )
}
function SparkIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M8 1.5 9.4 6.6 14.5 8 9.4 9.4 8 14.5 6.6 9.4 1.5 8 6.6 6.6 8 1.5Z" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round" />
    </svg>
  )
}
function WrenchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M10.8 2.7a3 3 0 0 0-3.9 3.7L2 11.3l1.7 1.7 4.9-4.9a3 3 0 0 0 3.7-3.9l-2 2-1.4-1.4 2-2Z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
    </svg>
  )
}
function ScaleIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M8 2v11.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <path d="M4.5 3.5h7" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <path d="M5.5 13.5h5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <path d="M2 6.2 4.5 3.5 7 6.2c0 1.4-1.1 2.3-2.5 2.3S2 7.6 2 6.2Z" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round" />
      <path d="M9 6.2 11.5 3.5 14 6.2c0 1.4-1.1 2.3-2.5 2.3S9 7.6 9 6.2Z" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round" />
    </svg>
  )
}
function DesktopIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="1.5" y="2.5" width="13" height="8.5" rx="1.2" stroke="currentColor" strokeWidth="1.3" />
      <path d="M5.5 14h5M8 11v3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  )
}
function DbIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <ellipse cx="8" cy="3.5" rx="5.5" ry="2" stroke="currentColor" strokeWidth="1.2" />
      <path d="M2.5 3.5V12c0 1.1 2.46 2 5.5 2s5.5-.9 5.5-2V3.5" stroke="currentColor" strokeWidth="1.2" />
      <path d="M2.5 7.75C2.5 8.85 4.96 9.75 8 9.75s5.5-.9 5.5-2" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  )
}
