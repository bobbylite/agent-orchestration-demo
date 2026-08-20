import { useEffect, useState } from "react"
import { api, type TokenLedgerEntry } from "../lib/api"

interface Props {
  onClose: () => void
}

const POLL_INTERVAL_MS = 2000

interface ChainData {
  user: TokenLedgerEntry | null
  agentOwn: TokenLedgerEntry | null
  delegation: TokenLedgerEntry | null
  taskAgentOwn: TokenLedgerEntry | null
  mcpRead: TokenLedgerEntry | null
  mcpWrite: TokenLedgerEntry | null
  mcpDelete: TokenLedgerEntry | null
}

const EMPTY: ChainData = {
  user: null,
  agentOwn: null,
  delegation: null,
  taskAgentOwn: null,
  mcpRead: null,
  mcpWrite: null,
  mcpDelete: null,
}

export function TokenChainPanel({ onClose }: Props) {
  const [data, setData] = useState<ChainData>(EMPTY)

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  useEffect(() => {
    let cancelled = false
    async function poll() {
      // Each service reports only what it's actually seen — never
      // synthesized just because this panel is open (see
      // backend/app/auth/token_ledger.py / task-agent/app/token_ledger.py).
      // Best-effort per service, same "one being down degrades gracefully"
      // pattern as TelemetryPanel.
      const [chatResult, taskResult] = await Promise.allSettled([api.getTokenChain(), api.getTaskAgentTokenChain()])
      if (cancelled) return
      setData({
        user: chatResult.status === "fulfilled" ? chatResult.value.user : null,
        agentOwn: chatResult.status === "fulfilled" ? chatResult.value.agent_own : null,
        delegation: chatResult.status === "fulfilled" ? chatResult.value.delegation : null,
        taskAgentOwn: taskResult.status === "fulfilled" ? taskResult.value.task_agent_own : null,
        mcpRead: taskResult.status === "fulfilled" ? taskResult.value.mcp_scoped_read : null,
        mcpWrite: taskResult.status === "fulfilled" ? taskResult.value.mcp_scoped_write : null,
        mcpDelete: taskResult.status === "fulfilled" ? taskResult.value.mcp_scoped_delete : null,
      })
    }
    poll()
    const id = setInterval(poll, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  const userSub = claimString(data.user, "sub")
  // "Identity preserved" only really means something once at least one
  // downstream token exists to compare against — a lone user token isn't a
  // chain yet.
  const subPreserved =
    Boolean(userSub) &&
    [data.delegation, data.mcpRead, data.mcpWrite].some((e) => e && claimString(e, "sub") === userSub)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[color-mix(in_srgb,var(--ink)_55%,transparent)] p-6 backdrop-blur-sm">
      <div className="flex h-full w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border bg-canvas shadow-floating">
        <div
          className="flex shrink-0 items-center justify-between border-b border-border px-6 py-4"
          style={{ backgroundImage: "linear-gradient(120deg, color-mix(in srgb, var(--brand) 10%, transparent), transparent 60%)" }}
        >
          <div>
            <h2 className="text-sm font-semibold tracking-tight text-ink">Token Chain — real, decoded JWTs</h2>
            <p className="text-xs text-ink-muted">
              Every RFC 8693 exchange in this session, in order — what was minted, what fed into what, and whether
              the human's identity survived the whole hop.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {subPreserved && (
              <span className="token-identity-chip hidden items-center gap-1.5 rounded-full border border-success/30 bg-success/10 px-2.5 py-1 text-[10.5px] font-semibold text-success sm:flex">
                <CheckIcon />
                sub preserved end-to-end
              </span>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close token chain"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-ink-muted transition hover:bg-code-bg hover:text-ink"
            >
              <CloseIcon />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <div className="mx-auto flex max-w-lg flex-col">
            <StageLabel text="Step 0 — the human signs in" />
            <TokenCard
              title="User Token"
              subtitle="PingOne · OIDC sign-in"
              accent="var(--ink-muted)"
              entry={data.user}
              placeholder="Sign in with PingOne to see your own token here."
            />

            <FlowLine active={Boolean(data.user)} />

            <div className="grid grid-cols-2 gap-3">
              <TokenCard
                compact
                title="Orchestration Agent Token"
                subtitle="Client Credentials"
                accent="var(--brand)"
                entry={data.agentOwn}
                placeholder="Not requested yet — click “Approve Agent Action” in chat."
              />
              <ExchangeGhostCard label="User Token (from above)" />
            </div>

            <ExchangeStage
              active={Boolean(data.agentOwn)}
              subject="User Token"
              actor="Orchestration Agent Token"
              resultLabel="Delegation Token"
            />

            <TokenCard
              title="Delegation Token"
              subtitle="scope: agent:delegation · addressed to the Task Agent"
              accent="var(--brand)"
              entry={data.delegation}
              placeholder="Not minted yet — this is the credential “Approve Agent Action” produces."
              highlightSubMatch={userSub}
            />

            <FlowLine active={Boolean(data.delegation)} />

            <div className="grid grid-cols-2 gap-3">
              <ExchangeGhostCard label="Delegation Token (from above)" />
              <TokenCard
                compact
                title="Task Agent Token"
                subtitle="Client Credentials"
                accent="var(--success)"
                entry={data.taskAgentOwn}
                placeholder="Not requested yet — ask the assistant something that needs the Task Agent."
              />
            </div>

            <ExchangeStage
              active={Boolean(data.taskAgentOwn)}
              subject="Delegation Token"
              actor="Task Agent Token"
              resultLabel="MCP-scoped Token (read or write, decided fresh per call)"
            />

            <div className="grid grid-cols-2 gap-3">
              <TokenCard
                compact
                title="MCP Token — todos:read"
                subtitle={data.mcpRead?.tool ? `last used by ${data.mcpRead.tool}` : "list_todos"}
                accent="var(--success)"
                entry={data.mcpRead}
                placeholder="Not used yet — ask to see your todos."
                highlightSubMatch={userSub}
              />
              <TokenCard
                compact
                title="MCP Token — todos:write"
                subtitle={data.mcpWrite?.tool ? `last used by ${data.mcpWrite.tool}` : "add_todo / complete_todo / reopen_todo"}
                accent="var(--warning)"
                entry={data.mcpWrite}
                placeholder="Not used yet — ask to add, complete, or reopen a todo."
                highlightSubMatch={userSub}
              />
              <TokenCard
                compact
                title="MCP Token — todos:delete"
                subtitle={data.mcpDelete?.tool ? `last used by ${data.mcpDelete.tool}` : "delete_todo"}
                accent="var(--danger)"
                entry={data.mcpDelete}
                placeholder="Not used yet — ask to delete a todo."
                highlightSubMatch={userSub}
              />
            </div>
            <p className="mt-4 text-center text-[10.5px] leading-relaxed text-ink-muted/70">
              Read, write, and delete tokens are independent — each is requested fresh, per call, from PingOne. One
              succeeding never implies the others were ever granted.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

function StageLabel({ text }: { text: string }) {
  return <p className="mb-2 text-center text-[10px] font-semibold tracking-wide text-ink-muted/70 uppercase">{text}</p>
}

function FlowLine({ active }: { active: boolean }) {
  return (
    <div className={`token-flow-line my-1 ${active ? "active" : ""}`}>{active && <span className="token-flow-dot" />}</div>
  )
}

function ExchangeGhostCard({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center rounded-xl border border-dashed border-border/70 px-3 py-3.5 text-center text-[10.5px] text-ink-muted/60">
      ↑ {label}
    </div>
  )
}

function ExchangeStage({
  active,
  subject,
  actor,
  resultLabel,
}: {
  active: boolean
  subject: string
  actor: string
  resultLabel: string
}) {
  return (
    <div className="my-2 flex flex-col items-center gap-1">
      <div className={`token-flow-line ${active ? "active" : ""}`}>{active && <span className="token-flow-dot" />}</div>
      <div
        className={`flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-[10.5px] font-semibold shadow-card transition ${
          active ? "border-brand/40 bg-brand/10 text-brand" : "border-border bg-canvas-raised text-ink-muted"
        }`}
      >
        <ExchangeIcon />
        RFC 8693 Token Exchange
      </div>
      <p className="max-w-xs text-center text-[9.5px] leading-snug text-ink-muted/70">
        subject = <span className="font-mono">{subject}</span> · actor = <span className="font-mono">{actor}</span>{" "}
        → <span className="font-mono">{resultLabel}</span>
      </p>
      <div className={`token-flow-line ${active ? "active" : ""}`}>{active && <span className="token-flow-dot" />}</div>
    </div>
  )
}

function claimString(entry: TokenLedgerEntry | null, key: string): string | null {
  const v = entry?.claims?.[key]
  return typeof v === "string" ? v : null
}

function formatClaimValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ")
  return String(value)
}

function formatExpiry(exp: unknown): { text: string; expired: boolean } | null {
  if (typeof exp !== "number") return null
  const remainingMs = exp * 1000 - Date.now()
  if (remainingMs <= 0) return { text: "expired", expired: true }
  const mins = Math.floor(remainingMs / 60000)
  const secs = Math.floor((remainingMs % 60000) / 1000)
  return { text: mins > 0 ? `expires in ${mins}m ${secs}s` : `expires in ${secs}s`, expired: false }
}

const HIGHLIGHT_CLAIM_ORDER = ["sub", "client_id", "agent_client_id", "scope", "aud"]

function TokenCard({
  title,
  subtitle,
  accent,
  entry,
  placeholder,
  compact,
  highlightSubMatch,
}: {
  title: string
  subtitle: string
  accent: string
  entry: TokenLedgerEntry | null
  placeholder: string
  compact?: boolean
  highlightSubMatch?: string | null
}) {
  const [revealed, setRevealed] = useState(false)
  const [copied, setCopied] = useState(false)

  if (!entry) {
    return (
      <div className="animate-pop-in rounded-xl border border-dashed border-border bg-canvas-raised/40 px-4 py-3.5">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 shrink-0 rounded-full bg-border" />
          <span className="text-[12.5px] font-semibold text-ink-muted">{title}</span>
        </div>
        <p className="mt-1.5 text-[10.5px] leading-snug text-ink-muted/70">{placeholder}</p>
      </div>
    )
  }

  const claims = entry.claims
  const exp = formatExpiry(claims.exp)
  const subMatches = highlightSubMatch != null && claimString(entry, "sub") === highlightSubMatch
  const otherKeys = Object.keys(claims)
    .filter((k) => !HIGHLIGHT_CLAIM_ORDER.includes(k) && k !== "exp" && k !== "iat" && k !== "nbf" && k !== "iss")
    .sort()

  async function copyToken() {
    try {
      await navigator.clipboard.writeText(entry!.raw)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard access can fail (permissions, insecure context) — not
      // worth surfacing as an error for a demo convenience button.
    }
  }

  return (
    <div
      className="animate-pop-in overflow-hidden rounded-xl border border-border bg-canvas-raised shadow-card"
      style={{ "--token-accent": accent } as React.CSSProperties}
    >
      <div className="token-card-accent h-[3px] w-full" />
      <div className={compact ? "px-3.5 py-3" : "px-4 py-3.5"}>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className={`${compact ? "text-[12px]" : "text-[13px]"} font-semibold text-ink`}>{title}</span>
              {subMatches && (
                <span title="Same sub as the user token — identity preserved through this exchange" className="text-success">
                  <LinkIcon />
                </span>
              )}
            </div>
            <p className="truncate text-[10px] text-ink-muted">{subtitle}</p>
          </div>
          {exp && (
            <span
              className={`shrink-0 rounded-md px-1.5 py-0.5 text-[9.5px] font-medium ${
                exp.expired ? "bg-danger-bg text-danger" : "bg-success/10 text-success"
              }`}
            >
              {exp.text}
            </span>
          )}
        </div>

        <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-2.5 gap-y-1 border-t border-border pt-2 font-mono text-[10px]">
          {HIGHLIGHT_CLAIM_ORDER.filter((k) => claims[k] !== undefined).map((k) => (
            <div key={k} className="contents">
              <dt className="whitespace-nowrap text-ink-muted">{k}</dt>
              <dd className="min-w-0 truncate text-ink" title={formatClaimValue(claims[k])}>
                {formatClaimValue(claims[k])}
              </dd>
            </div>
          ))}
        </dl>

        {otherKeys.length > 0 && (
          <details className="mt-2 text-[10px]">
            <summary className="cursor-pointer text-ink-muted select-none">+{otherKeys.length} more claims</summary>
            <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-2.5 gap-y-1 font-mono">
              {otherKeys.map((k) => (
                <div key={k} className="contents">
                  <dt className="whitespace-nowrap text-ink-muted">{k}</dt>
                  <dd className="min-w-0 truncate text-ink" title={formatClaimValue(claims[k])}>
                    {formatClaimValue(claims[k])}
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        )}

        <div className="mt-2.5 flex items-center gap-2 border-t border-border pt-2">
          <button
            type="button"
            onClick={() => setRevealed((r) => !r)}
            className="text-[10px] font-semibold text-brand transition hover:text-brand/80"
          >
            {revealed ? "Hide raw token" : "Reveal raw token"}
          </button>
          {revealed && (
            <button
              type="button"
              onClick={copyToken}
              className="text-[10px] font-medium text-ink-muted transition hover:text-ink"
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          )}
        </div>
        {revealed && (
          <p className="mt-1.5 max-h-16 overflow-y-auto rounded-md bg-code-bg px-2 py-1.5 font-mono text-[9px] break-all text-ink-muted">
            {entry.raw}
          </p>
        )}
      </div>
    </div>
  )
}

function CloseIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3 3L13 13M13 3L3 13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

function ExchangeIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M2 5.5h9.5M11.5 5.5 8.5 2.5M11.5 5.5 8.5 8.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M14 10.5H4.5M4.5 10.5 7.5 7.5M4.5 10.5 7.5 13.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3 8.5 6.5 12 13 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function LinkIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M6.5 9.5 9.5 6.5M6 4.5 7.2 3.3a2.3 2.3 0 0 1 3.3 3.3L9.3 7.8M10 11.5l-1.2 1.2a2.3 2.3 0 0 1-3.3-3.3L6.7 8.2"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
