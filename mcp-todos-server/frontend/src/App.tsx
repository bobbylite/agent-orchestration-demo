import { useCallback, useEffect, useState } from "react"
import { AuditLogPanel } from "./components/AuditLogPanel"
import { SignInButton } from "./components/SignInButton"
import { ThemeToggle } from "./components/ThemeToggle"
import { TodoPanel } from "./components/TodoPanel"
import { api, type MeResponse } from "./lib/api"

export default function App() {
  const [me, setMe] = useState<MeResponse | null>(null)

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
    <div className="flex h-screen flex-col overflow-hidden bg-canvas text-ink">
      <header className="z-10 flex shrink-0 items-center justify-between border-b border-border bg-canvas-raised px-7 py-4 shadow-card">
        <div>
          <h1 className="text-[15px] font-semibold tracking-tight">Todos — Agent Orchestration Console</h1>
          <p className="text-xs text-ink-muted">MCP tool server · PingOne identity · OBO audit log</p>
        </div>
        <div className="flex items-center gap-3">
          <SignInButton me={me} />
          <div className="mx-1 h-6 w-px bg-border" aria-hidden="true" />
          <ThemeToggle />
        </div>
      </header>

      <main className="flex min-h-0 flex-1">
        <section className="min-h-0 flex-1 border-r border-border bg-canvas-raised">
          <TodoPanel signedIn={signedIn} />
        </section>

        <aside className="flex min-h-0 w-[30rem] shrink-0 flex-col bg-canvas-raised">
          <AuditLogPanel signedIn={signedIn} />
        </aside>
      </main>
    </div>
  )
}
