import { useCallback, useEffect, useState, type FormEvent } from "react"
import { api, type Todo } from "../lib/api"
import { RefreshButton } from "./RefreshButton"
import { TodoItem } from "./TodoItem"

interface Props {
  signedIn: boolean
  /** Called after a todo is added or completed here — lets the parent
   * refresh anything else that should reflect it (the audit log) without
   * either panel polling on a timer. */
  onActivity: () => void
}

export function TodoPanel({ signedIn, onActivity }: Props) {
  const [todos, setTodos] = useState<Todo[]>([])
  const [draft, setDraft] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const refresh = useCallback(async () => {
    setRefreshing(true)
    try {
      const { todos: latest } = await api.getTodos()
      setTodos(latest)
    } catch {
      // best-effort; the user can retry via the refresh button
    } finally {
      setRefreshing(false)
    }
  }, [])

  // Fetched once on sign-in, not polled — an agent-driven change made
  // from the chat app in another tab won't appear until an action here or
  // a manual refresh, by design (see CLAUDE.md).
  useEffect(() => {
    if (signedIn) refresh()
  }, [signedIn, refresh])

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || submitting) return
    setSubmitting(true)
    try {
      const todo = await api.addTodo(text)
      setTodos((prev) => [...prev, todo])
      setDraft("")
      onActivity()
    } catch {
      // best-effort; a manual refresh will reconcile either way
    } finally {
      setSubmitting(false)
    }
  }

  async function handleComplete(id: string) {
    const previous = todos.find((todo) => todo.id === id)
    setTodos((prev) => prev.map((t) => (t.id === id ? { ...t, done: true } : t)))
    try {
      await api.completeTodo(id)
      onActivity()
    } catch {
      if (previous) setTodos((prev) => prev.map((t) => (t.id === id ? previous : t)))
    }
  }

  async function handleReopen(id: string) {
    const previous = todos.find((todo) => todo.id === id)
    setTodos((prev) => prev.map((t) => (t.id === id ? { ...t, done: false } : t)))
    try {
      await api.reopenTodo(id)
      onActivity()
    } catch {
      if (previous) setTodos((prev) => prev.map((t) => (t.id === id ? previous : t)))
    }
  }

  async function handleDelete(id: string) {
    const previous = todos.find((todo) => todo.id === id)
    if (!previous || !window.confirm(`Delete “${previous.text}”? This cannot be undone.`)) return
    setTodos((prev) => prev.filter((todo) => todo.id !== id))
    try {
      await api.deleteTodo(id)
      onActivity()
    } catch {
      setTodos((prev) => (prev.some((todo) => todo.id === id) ? prev : [...prev, previous]))
    }
  }

  if (!signedIn) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
        <div className="flex h-11 w-11 items-center justify-center rounded-full bg-code-bg text-ink-muted">
          <ListIcon />
        </div>
        <div className="max-w-64">
          <p className="text-sm font-medium text-ink">Sign in to view todos</p>
          <p className="mt-1 text-xs text-ink-muted">
            Todos created directly by you and on your behalf by the agent both show up here once you&rsquo;re
            signed in.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-3.5 sm:px-5">
        <h2 className="text-xs font-semibold tracking-wide text-ink-muted uppercase">Todos</h2>
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-code-bg px-2 py-0.5 font-mono text-[10px] text-ink-muted">
            {todos.length}
          </span>
          <RefreshButton onClick={refresh} refreshing={refreshing} label="Refresh todos" />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-4 sm:px-5">
        {todos.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <p className="text-sm text-ink-muted">No todos yet.</p>
            <p className="max-w-52 text-xs text-ink-muted/70">Add one below, or ask the agent in the chat console.</p>
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {todos.map((todo) => (
              <TodoItem key={todo.id} todo={todo} onComplete={handleComplete} onReopen={handleReopen} onDelete={handleDelete} />
            ))}
          </ul>
        )}
      </div>

      <form onSubmit={handleSubmit} className="flex shrink-0 gap-2.5 border-t border-border p-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:p-4">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Add a todo…"
          className="min-w-0 min-h-11 flex-1 rounded-md border border-border bg-canvas-raised px-4 py-2.5 text-sm text-ink placeholder:text-ink-muted transition focus:border-brand focus:outline-none"
        />
        <button
          type="submit"
          disabled={!draft.trim() || submitting}
          style={draft.trim() && !submitting ? { backgroundImage: "var(--brand-gradient)" } : undefined}
          className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-md bg-brand text-brand-ink shadow-card transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
          aria-label="Add todo"
        >
          <PlusIcon />
        </button>
      </form>
    </div>
  )
}

function ListIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M6 5.5H16.5M6 10H16.5M6 14.5H16.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="3" cy="5.5" r="1" fill="currentColor" />
      <circle cx="3" cy="10" r="1" fill="currentColor" />
      <circle cx="3" cy="14.5" r="1" fill="currentColor" />
    </svg>
  )
}

function PlusIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path d="M9 3.5V14.5M3.5 9H14.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}
