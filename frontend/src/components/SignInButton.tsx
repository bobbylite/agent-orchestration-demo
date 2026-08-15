import type { MeResponse } from "../lib/api"
import { api } from "../lib/api"

interface Props {
  me: MeResponse | null
}

function initials(name: string | null, email: string | null): string {
  const source = name ?? email ?? "?"
  const parts = source.trim().split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return source.slice(0, 2).toUpperCase()
}

export function SignInButton({ me }: Props) {
  if (!me?.oidc_enabled) {
    return (
      <span
        className="text-xs text-ink-muted"
        title="Set OIDC_DISCOVERY_URL / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET / SESSION_SECRET"
      >
        PingOne sign-in not configured
      </span>
    )
  }

  if (me.signed_in) {
    return (
      <div className="flex items-center gap-2.5">
        <div
          style={{ backgroundImage: "var(--brand-gradient)" }}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold text-brand-ink"
        >
          {initials(me.name, me.email)}
        </div>
        <div className="leading-tight">
          <div className="text-sm font-medium text-ink">{me.name ?? me.email ?? "Signed in"}</div>
          <div className="max-w-40 truncate font-mono text-[11px] text-ink-muted" title={me.sub ?? undefined}>
            {me.sub}
          </div>
        </div>
        <a
          href={api.logoutUrl}
          className="ml-1 rounded-md border border-border px-3 py-1.5 text-sm font-medium text-ink-muted transition hover:border-danger hover:text-danger"
        >
          Sign out
        </a>
      </div>
    )
  }

  return (
    <a
      href={api.loginUrl}
      style={{ backgroundImage: "var(--brand-gradient)" }}
      className="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-semibold text-brand-ink shadow-raised transition hover:opacity-90"
    >
      Sign in with PingOne
    </a>
  )
}
