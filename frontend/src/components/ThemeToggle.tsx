import { useEffect, useState } from "react"
import { applyTheme, resolvedTheme, type Theme } from "../lib/theme"

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light")

  useEffect(() => {
    setTheme(resolvedTheme())
  }, [])

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark"
    applyTheme(next)
    setTheme(next)
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle color theme"
      className="flex h-8 w-8 items-center justify-center rounded-full border border-border text-ink-muted transition hover:border-brand hover:text-ink"
    >
      {theme === "dark" ? "🌙" : "☀️"}
    </button>
  )
}
