import { useCallback, useEffect, useState } from "react"
import { AuditLogPanel } from "./components/AuditLogPanel"
import { SignInButton } from "./components/SignInButton"
import { ThemeToggle } from "./components/ThemeToggle"
import { TodoPanel } from "./components/TodoPanel"
import { api, type MeResponse } from "./lib/api"

export default function App() {
  const [me, setMe] = useState<MeResponse | null>(null)
  // Bumped whenever something worth showing in the audit log just
  // happened (an add/complete in this UI) or the user asks for a manual
  // refresh — AuditLogPanel re-fetches when this changes instead of
  // polling on a fixed interval. See CLAUDE.md/README: only meaningful
  // actions refresh the log, not a timer.
  const [activityVersion, setActivityVersion] = useState(0)
  const bumpActivity = useCallback(() => setActivityVersion((v) => v + 1), [])

  const refreshMe = useCallback(() => {
    return api
      .getMe()
      .then(setMe)
      .catch(() => setMe(null))
  }, [])

  useEffect(() => {
    refreshMe()
  }, [refreshMe])

  const signedIn = Boolean(me?.signed_in)

  return (
    <div className="flex h-[100dvh] min-h-[100dvh] min-w-0 flex-col overflow-hidden bg-canvas text-ink">
      <header className="z-10 flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border bg-canvas-raised px-3 py-3 shadow-card sm:flex-nowrap sm:px-7 sm:py-4">
        <div className="min-w-0">
          <h1 className="truncate text-xs font-semibold tracking-tight sm:text-[15px]">Todos — Agent Orchestration Console</h1>
          <p className="hidden text-xs text-ink-muted sm:block">MCP tool server · PingOne identity · OBO audit log</p>
        </div>
        <div className="flex items-center gap-3">
          <SignInButton me={me} />
          <div className="mx-1 h-6 w-px bg-border" aria-hidden="true" />
          <ThemeToggle />
        </div>
      </header>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col xl:flex-row">
        <section className="min-h-0 min-w-0 flex-1 border-b border-border bg-canvas-raised xl:border-b-0 xl:border-r">
          <TodoPanel signedIn={signedIn} onActivity={bumpActivity} />
        </section>

        <aside className="flex h-[min(42dvh,30rem)] min-h-0 w-full shrink-0 flex-col bg-canvas-raised xl:h-auto xl:w-[30rem]">
          <AuditLogPanel signedIn={signedIn} refreshSignal={activityVersion} />
        </aside>
      </main>
    </div>
  )
}
