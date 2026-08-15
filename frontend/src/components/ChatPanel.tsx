import { useState, type FormEvent } from "react"

export interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
}

interface Props {
  messages: ChatMessage[]
  canSend: boolean
  disabledReason: string | null
  onSend: (message: string) => void
}

export function ChatPanel({ messages, canSend, disabledReason, onSend }: Props) {
  const [draft, setDraft] = useState("")

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const trimmed = draft.trim()
    if (!trimmed || !canSend) return
    onSend(trimmed)
    setDraft("")
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-ink-muted">
            {disabledReason ?? "Send a message to start chatting with the agent."}
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {messages.map((message) => (
              <div key={message.id} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[75%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                    message.role === "user"
                      ? "bg-brand text-brand-ink"
                      : "border border-border bg-canvas-raised text-ink"
                  }`}
                >
                  {message.content || (message.role === "assistant" ? "…" : "")}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <form onSubmit={handleSubmit} className="flex gap-2 border-t border-border p-4">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={canSend ? "Message the agent…" : (disabledReason ?? "Sign in to chat")}
          disabled={!canSend}
          className="flex-1 rounded-full border border-border bg-canvas-raised px-4 py-2.5 text-sm text-ink placeholder:text-ink-muted focus:border-brand focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={!canSend || !draft.trim()}
          className="rounded-full bg-brand px-5 py-2.5 text-sm font-medium text-brand-ink transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
  )
}
