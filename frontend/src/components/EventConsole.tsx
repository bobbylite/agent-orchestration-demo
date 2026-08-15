export interface RawEvent {
  id: string
  event: string
  data: string
  at: number
}

interface Props {
  events: RawEvent[]
}

export function EventConsole({ events }: Props) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between px-5 py-3.5">
        <h2 className="text-xs font-semibold tracking-wide text-ink-muted uppercase">Raw Event Stream</h2>
        <span className="rounded-full bg-code-bg px-2 py-0.5 font-mono text-[10px] text-ink-muted">
          {events.length}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
        <div className="min-h-full rounded-lg bg-code-bg/50 px-4 py-3 font-mono text-[11px] leading-relaxed">
          {events.length === 0 ? (
            <p className="text-ink-muted">No stream activity yet.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {events.map((event) => (
                <li key={event.id} className="break-all text-ink-muted">
                  <span className="text-brand">{event.event}</span> <span className="text-ink">{event.data}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
