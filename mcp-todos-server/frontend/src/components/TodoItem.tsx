import type { Todo } from "../lib/api"
import { CreatorBadge } from "./CreatorBadge"

interface Props {
  todo: Todo
  onComplete: (id: string) => void
  onReopen: (id: string) => void
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    })
  } catch {
    return iso
  }
}

export function TodoItem({ todo, onComplete, onReopen }: Props) {
  const creator = todo.creator_label ?? todo.creator_sub ?? "Unknown"

  return (
    <li className="animate-pop-in flex items-start gap-3 rounded-xl border border-border bg-canvas-raised p-3.5 shadow-card">
      <button
        type="button"
        onClick={() => (todo.done ? onReopen(todo.id) : onComplete(todo.id))}
        aria-label={todo.done ? "Undo completion" : "Mark as done"}
        className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition ${
          todo.done
            ? "border-success bg-success text-canvas-raised"
            : "border-border text-transparent hover:border-brand"
        }`}
      >
        <CheckIcon />
      </button>

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <p className={`text-sm ${todo.done ? "text-ink-muted line-through" : "text-ink"}`}>{todo.text}</p>
          <CreatorBadge createdBy={todo.created_by} />
        </div>
        <p className="mt-1 truncate text-[11px] text-ink-muted" title={creator}>
          {todo.created_by === "agent" ? "On behalf of " : "by "}
          <span className="font-mono">{creator}</span>
          {" · "}
          {formatTime(todo.created_at)}
        </p>
      </div>
    </li>
  )
}

function CheckIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M2 6.2 4.8 9 10 3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
