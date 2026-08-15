const DELAYS_MS = [0, 160, 320]

export function TypingIndicator() {
  return (
    <span className="inline-flex items-center gap-1 py-0.5">
      {DELAYS_MS.map((delay) => (
        <span
          key={delay}
          className="h-1.5 w-1.5 rounded-full bg-ink-muted [animation:typing-bounce_1.2s_ease-in-out_infinite]"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  )
}
