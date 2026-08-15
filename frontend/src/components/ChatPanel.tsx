import { useState, type FormEvent } from "react"
import { Markdown } from "./Markdown"
import { TypingIndicator } from "./TypingIndicator"

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
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-code-bg text-ink-muted">
              <ChatIcon />
            </div>
            <div className="max-w-72">
              <p className="text-sm font-medium text-ink">
                {canSend ? "Start a conversation" : "Waiting to connect"}
              </p>
              <p className="mt-1 text-xs text-ink-muted">
                {disabledReason ?? "Send a message to start chatting with the agent."}
              </p>
            </div>
          </div>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            {messages.map((message) => (
              <div
                key={message.id}
                className={`flex animate-pop-in ${message.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[75%] rounded-xl px-4 py-2.5 ${
                    message.role === "user"
                      ? "whitespace-pre-wrap bg-brand text-sm leading-relaxed text-brand-ink shadow-card"
                      : "border border-border bg-canvas-raised text-ink shadow-card"
                  }`}
                >
                  {message.role === "assistant" ? (
                    message.content ? (
                      <Markdown content={message.content} />
                    ) : (
                      <TypingIndicator />
                    )
                  ) : (
                    message.content
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <form onSubmit={handleSubmit} className="mx-auto flex w-full max-w-3xl gap-2.5 border-t border-border p-4">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={canSend ? "Message the agent…" : (disabledReason ?? "Sign in to chat")}
          disabled={!canSend}
          className="flex-1 rounded-md border border-border bg-canvas-raised px-4 py-2.5 text-sm text-ink placeholder:text-ink-muted transition focus:border-brand focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={!canSend || !draft.trim()}
          aria-label="Send message"
          style={canSend && draft.trim() ? { backgroundImage: "var(--brand-gradient)" } : undefined}
          className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-md bg-brand text-brand-ink shadow-card transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
        >
          <SendIcon />
        </button>
      </form>
    </div>
  )
}

function ChatIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path
        d="M2.5 5.5C2.5 4.4 3.4 3.5 4.5 3.5H15.5C16.6 3.5 17.5 4.4 17.5 5.5V12.5C17.5 13.6 16.6 14.5 15.5 14.5H8L4.5 17V14.5H4.5C3.4 14.5 2.5 13.6 2.5 12.5V5.5Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function SendIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path
        d="M15.5 2.5L8.25 9.75M15.5 2.5L11 15.5L8.25 9.75M15.5 2.5L2.5 7L8.25 9.75"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
