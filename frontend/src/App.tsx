import { useCallback, useEffect, useRef, useState } from "react"
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
    return api
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

  function updateMessage(id: string, patch: Partial<ChatMessage>) {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)))
  }

  // Shared by the initial send and the inline-approval retry — both are
  // "run this user text through the graph and stream into this assistant
  // message", just with different setup around them.
  async function runTurn(content: string, assistantMessageId: string) {
    setSending(true)
    try {
      await streamInvoke(threadId.current, content, {
        onToken: (text) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantMessageId ? { ...m, content: m.content + text } : m)),
          )
        },
        onAuthRequired: () => updateMessage(assistantMessageId, { needsApproval: true }),
        onDone: () => setSending(false),
        onError: (message) => {
          updateMessage(assistantMessageId, { content: `⚠ ${message}` })
          setSending(false)
        },
        onRawEvent: pushRawEvent,
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      updateMessage(assistantMessageId, { content: `⚠ ${message}` })
      setSending(false)
    }
  }

  async function handleSend(content: string) {
    const userMessage: ChatMessage = { id: newId(), role: "user", content }
    const assistantMessage: ChatMessage = { id: newId(), role: "assistant", content: "", sourceUserContent: content }
    setMessages((prev) => [...prev, userMessage, assistantMessage])
    await runTurn(content, assistantMessage.id)
  }

  // Approve inline (Client Credentials + RFC 8693 Token Exchange), then
  // automatically retry the same request — "ask → approve → try again" as
  // one flow, not a second manual step.
  async function handleInlineApprove(assistantMessageId: string, originalContent: string) {
    await api.approveAgentAction()
    await refreshMe()
    updateMessage(assistantMessageId, { needsApproval: false, content: "" })
    await runTurn(originalContent, assistantMessageId)
  }

  // Plain chat only needs a signed-in session — see backend/app/routes/invoke.py.
  // Delegating to the Task Agent (todos, etc.) needs a per-action delegated
  // token too, but that's surfaced inline in the chat when it's actually
  // needed, scoped to exactly that action — not gated here.
  const canSend = Boolean(me?.signed_in) && !sending
  const disabledReason = !me?.oidc_enabled
    ? null
    : !me?.signed_in
      ? "Sign in with PingOne to start chatting."
      : null

  return (
    <div className="flex h-[100dvh] min-h-[100dvh] min-w-0 flex-col overflow-hidden bg-canvas text-ink">
      <header className="z-10 flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border bg-canvas-raised px-3 py-3 shadow-card sm:flex-nowrap sm:px-5 sm:py-4 md:px-7">
        <div className="min-w-0">
          <h1 className="truncate text-xs font-semibold tracking-tight sm:text-[15px]">Multi Agent Orchestration | Digital Assistant</h1>
          <p className="hidden text-xs text-ink-muted sm:block">LangChain · PingOne identity · OpenTelemetry</p>
        </div>
        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <SignInButton me={me} />
          <div className="mx-1 h-6 w-px bg-border" aria-hidden="true" />
          <ThemeToggle />
        </div>
      </header>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col xl:flex-row">
        <section className="min-h-0 min-w-0 flex-1">
          <ChatPanel
            messages={messages}
            canSend={canSend}
            disabledReason={disabledReason}
            onSend={handleSend}
            onInlineApprove={handleInlineApprove}
          />
        </section>

        <aside className="flex h-[min(42dvh,30rem)] min-h-0 w-full shrink-0 flex-col border-t border-border bg-canvas-raised xl:h-auto xl:w-[30rem] xl:border-t-0 xl:border-l">
          <div className="min-h-0 flex-[3]">
            <TelemetryPanel />
          </div>
          <div className="min-h-0 flex-1 border-t border-border">
            <EventConsole events={rawEvents} />
          </div>
        </aside>
      </main>
    </div>
  )
}
