import type { MeResponse } from "../lib/api"
import { api } from "../lib/api"

interface Props {
  me: MeResponse | null
}

export function SignInButton({ me }: Props) {
  if (!me?.oidc_enabled) {
    return (
      <span className="text-xs text-ink-muted" title="Set OIDC_DISCOVERY_URL / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET / SESSION_SECRET">
        PingOne sign-in not configured
      </span>
    )
  }

  if (me.signed_in) {
    return (
      <div className="flex items-center gap-3">
        <div className="text-right leading-tight">
          <div className="text-sm font-medium text-ink">{me.name ?? me.email ?? "Signed in"}</div>
          <div className="text-xs text-ink-muted">{me.sub}</div>
        </div>
        <a
          href={api.logoutUrl}
          className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-ink-muted transition hover:border-danger hover:text-danger"
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
      className="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-semibold text-brand-ink shadow-sm transition hover:opacity-90"
    >
      Sign in with PingOne
    </a>
  )
}
