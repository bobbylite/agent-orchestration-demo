import { useCallback, useEffect, useRef, useState } from "react"
import { AgentAuthButton } from "./components/AgentAuthButton"
import { ChatPanel, type ChatMessage } from "./components/ChatPanel"
import { EventConsole, type RawEvent } from "./components/EventConsole"
import { SignInButton } from "./components/SignInButton"
import { TelemetryPanel } from "./components/TelemetryPanel"
import { ThemeToggle } from "./components/ThemeToggle"
import { api, streamInvoke, type MeResponse } from "./lib/api"

function newId(): string {
  return crypto.randomUUID()
}

export default function App() {
  const [me, setMe] = useState<MeResponse | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [rawEvents, setRawEvents] = useState<RawEvent[]>([])
  const [sending, setSending] = useState(false)
  const threadId = useRef(newId())

  const refreshMe = useCallback(() => {
    api
      .getMe()
      .then(setMe)
      .catch(() => setMe(null))
  }, [])

  useEffect(() => {
    refreshMe()
  }, [refreshMe])

  function pushRawEvent(event: string, data: string) {
    setRawEvents((prev) => [...prev.slice(-199), { id: newId(), event, data, at: Date.now() }])
  }

  async function handleSend(content: string) {
    const userMessage: ChatMessage = { id: newId(), role: "user", content }
    const assistantMessage: ChatMessage = { id: newId(), role: "assistant", content: "" }
    setMessages((prev) => [...prev, userMessage, assistantMessage])
    setSending(true)

    await streamInvoke(threadId.current, content, {
      onToken: (text) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantMessage.id ? { ...m, content: m.content + text } : m)),
        )
      },
      onDone: () => setSending(false),
      onError: (message) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantMessage.id ? { ...m, content: `⚠ ${message}` } : m)),
        )
        setSending(false)
      },
      onRawEvent: pushRawEvent,
    })
  }

  const canSend = Boolean(me?.signed_in) && !sending
  const disabledReason = !me?.oidc_enabled
    ? null
    : !me?.signed_in
      ? "Sign in with PingOne to start chatting."
      : null

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-canvas text-ink">
      <header className="flex shrink-0 items-center justify-between border-b border-border px-6 py-4">
        <div>
          <h1 className="text-base font-semibold">AgentCore Console</h1>
          <p className="text-xs text-ink-muted">LangGraph agent · PingOne identity · OpenTelemetry</p>
        </div>
        <div className="flex items-center gap-4">
          <AgentAuthButton me={me} onAuthenticated={refreshMe} />
          <SignInButton me={me} />
          <ThemeToggle />
        </div>
      </header>

      <main className="flex min-h-0 flex-1">
        <section className="min-h-0 flex-1 border-r border-border">
          <ChatPanel messages={messages} canSend={canSend} disabledReason={disabledReason} onSend={handleSend} />
        </section>

        <aside className="flex min-h-0 w-96 shrink-0 flex-col divide-y divide-border">
          <div className="min-h-0 flex-1">
            <TelemetryPanel />
          </div>
          <div className="min-h-0 flex-1">
            <EventConsole events={rawEvents} />
          </div>
        </aside>
      </main>
    </div>
  )
}
