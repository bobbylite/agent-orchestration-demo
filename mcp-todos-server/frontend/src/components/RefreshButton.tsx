interface Props {
  onClick: () => void
  refreshing: boolean
  label: string
}

export function RefreshButton({ onClick, refreshing, label }: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={refreshing}
      aria-label={label}
      title={label}
      className="touch-target flex h-11 w-11 shrink-0 items-center justify-center sm:h-8 sm:w-8 rounded-full text-ink-muted transition hover:bg-code-bg hover:text-ink disabled:opacity-50"
    >
      <RefreshIcon spinning={refreshing} />
    </button>
  )
}

function RefreshIcon({ spinning }: { spinning: boolean }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      className={spinning ? "animate-spin" : undefined}
    >
      <path
        d="M13.65 4A6 6 0 1 0 14 8"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <path d="M13.5 1.5V4.5H10.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
