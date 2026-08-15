import type { MeResponse } from "../lib/api"
import { api } from "../lib/api"

interface Props {
  me: MeResponse | null
}

function initials(name: string | null, email: string | null): string | null {
  const source = name ?? email
  if (!source) return null
  const parts = source.trim().split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return source.slice(0, 2).toUpperCase()
}

// Fallback shown in the avatar when there are no initials to display.
function PersonIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="currentColor"
      className="h-5 w-5"
      aria-hidden="true"
    >
      <path d="M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z" />
      <path d="M12 14c-5.33 0-8 2.67-8 4v1h16v-1c0-1.33-2.67-4-8-4Z" />
    </svg>
  )
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
    const label = initials(me.name, me.email)
    return (
      <div className="flex items-center gap-2.5">
        <div
          style={{ backgroundImage: "var(--brand-gradient)" }}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold text-brand-ink"
        >
          {label ?? <PersonIcon />}
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
