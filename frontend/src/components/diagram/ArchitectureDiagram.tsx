import { useEffect, useMemo } from "react"
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeTypes,
  getSmoothStepPath,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import type { TelemetrySpan } from "../../lib/api"
import { isRecent, latestSpan, STORY_EDGES, STORY_NODES, type StoryNodeData } from "./flowConfig"
import { StoryNode } from "./StoryNode"

interface Props {
  spans: TelemetrySpan[]
  onClose: () => void
}

function StoryEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd }: EdgeProps) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 12,
  })
  const meta = data as { label: string; detail: string; live: boolean; active: boolean }

  return (
    <>
      <path
        id={id}
        d={path}
        fill="none"
        markerEnd={markerEnd as string}
        strokeWidth={meta.active ? 2.25 : meta.live ? 1.6 : 1.4}
        stroke={meta.live ? "var(--brand)" : "var(--border)"}
        strokeDasharray={meta.active ? "6 4" : meta.live ? undefined : "4 4"}
        opacity={meta.live ? (meta.active ? 1 : 0.55) : 0.55}
        className={meta.active ? "diagram-edge-flow diagram-edge-glow" : undefined}
      />
      {meta.active && (
        // Two particles, offset by half a cycle, so the "data actually
        // moving" read stays continuous rather than one dot chasing an
        // empty gap.
        <>
          <circle r="3.5" fill="var(--brand)" className="diagram-edge-particle">
            <animateMotion dur="1.3s" repeatCount="indefinite" path={path} />
          </circle>
          <circle r="3.5" fill="var(--brand)" className="diagram-edge-particle" style={{ animationDelay: "0.65s" }}>
            <animateMotion dur="1.3s" repeatCount="indefinite" path={path} begin="0.65s" />
          </circle>
        </>
      )}
      <foreignObject x={labelX - 120} y={labelY - 24} width={240} height={48} className="pointer-events-none overflow-visible">
        <div className="flex justify-center">
          <span
            title={meta.detail}
            className="pointer-events-auto max-w-[230px] whitespace-normal rounded-md border border-border bg-canvas px-2 py-1 text-center text-[9.5px] font-medium leading-tight text-ink-muted shadow-card"
          >
            {meta.label}
          </span>
        </div>
      </foreignObject>
    </>
  )
}

const edgeTypes = { story: StoryEdge }

export function ArchitectureDiagram({ spans, onClose }: Props) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const nodeTypes = useMemo<NodeTypes>(
    () => ({
      story: (props) => <StoryNode {...props} data={props.data as StoryNodeData} spans={spans} />,
    }),
    [spans],
  )

  const nodes: Node[] = useMemo(
    () =>
      STORY_NODES.map((n) => ({
        id: n.id,
        type: "story",
        position: n.position,
        data: n.data,
        draggable: false,
        selectable: false,
      })),
    [],
  )

  const edges: Edge[] = useMemo(
    () =>
      STORY_EDGES.map((e) => {
        const match = e.meta.spanName ? latestSpan(spans, [e.meta.spanName], e.meta.service) : null
        const active = isRecent(match)
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          sourceHandle: e.sourceHandle,
          targetHandle: e.targetHandle,
          type: "story",
          markerEnd: { type: MarkerType.ArrowClosed, color: e.meta.spanName ? "var(--brand)" : "var(--border)", width: 16, height: 16 },
          data: { label: e.label, detail: e.detail, live: Boolean(e.meta.spanName), active },
        }
      }),
    [spans],
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[color-mix(in_srgb,var(--ink)_55%,transparent)] p-6 backdrop-blur-sm">
      <div className="flex h-full w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-border bg-canvas shadow-floating">
        <div className="flex shrink-0 items-center justify-between border-b border-border bg-canvas-raised px-6 py-4">
          <div>
            <h2 className="text-sm font-semibold tracking-tight text-ink">How this works — identity &amp; data flow</h2>
            <p className="text-xs text-ink-muted">
              Two front doors, one security chain — every hop re-verifies its own token fresh, no matter which
              orchestrator the request came through.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close diagram"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-ink-muted transition hover:bg-code-bg hover:text-ink"
          >
            <CloseIcon />
          </button>
        </div>

        <div className="relative min-h-0 flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            nodesConnectable={false}
            nodesDraggable={false}
            edgesFocusable={false}
            proOptions={{ hideAttribution: true }}
            minZoom={0.4}
            maxZoom={1.5}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--border)" />
            <Controls showInteractive={false} className="diagram-controls" />
          </ReactFlow>

          <div className="pointer-events-none absolute bottom-4 left-4 w-64 rounded-xl border border-border bg-canvas-raised/95 p-3.5 text-[11px] shadow-raised backdrop-blur">
            <p className="mb-2 text-[10px] font-semibold tracking-wide text-ink-muted uppercase">Identity structure</p>
            <dl className="flex flex-col gap-1.5 font-mono text-[10.5px]">
              <div className="flex justify-between gap-2">
                <dt className="text-ink-muted">sub</dt>
                <dd className="text-ink">the human</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-ink-muted">client_id</dt>
                <dd className="text-ink">the agent</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-ink-muted">scope</dt>
                <dd className="text-ink">what it may do</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-ink-muted">aud</dt>
                <dd className="text-ink">valid for whom</dd>
              </div>
            </dl>
            <p className="mt-2 border-t border-border pt-2 text-[10px] leading-snug text-ink-muted/80">
              Each hop mints its <em>own</em> token, scoped to exactly what it needs — never one token forwarded
              unchanged. Every service verifies all four independently, regardless of who asked.
            </p>
          </div>

          <div className="pointer-events-none absolute bottom-4 right-4 flex items-center gap-3 rounded-xl border border-border bg-canvas-raised/95 px-3.5 py-2 text-[10.5px] text-ink-muted shadow-raised backdrop-blur">
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-6 rounded-full" style={{ backgroundColor: "var(--brand)" }} />
              live in this panel
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-0 w-6 border-t-2 border-dashed" style={{ borderColor: "var(--border)" }} />
              happens, not aggregated here
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

function CloseIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3 3L13 13M13 3L3 13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}
