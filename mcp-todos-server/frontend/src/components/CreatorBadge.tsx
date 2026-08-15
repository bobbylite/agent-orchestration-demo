import type { CreatedBy } from "../lib/api"

export function CreatorBadge({ createdBy }: { createdBy: CreatedBy }) {
  const isAgent = createdBy === "agent"
  return (
    <span
      className={`shrink-0 rounded-md px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase ${
        isAgent ? "bg-brand/15 text-brand" : "bg-code-bg text-ink-muted"
      }`}
    >
      {isAgent ? "Agent" : "Human"}
    </span>
  )
}
