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
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">Raw Event Stream</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 font-mono text-[11px]">
        {events.length === 0 ? (
          <p className="text-ink-muted">No stream activity yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {events.map((event) => (
              <li key={event.id} className="text-ink-muted">
                <span className="text-brand">{event.event}</span>{" "}
                <span className="text-ink">{event.data}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
