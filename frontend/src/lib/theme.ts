export type Theme = "light" | "dark"

export function getStoredTheme(): Theme | null {
  const value = localStorage.getItem("theme")
  return value === "light" || value === "dark" ? value : null
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme
  localStorage.setItem("theme", theme)
}

export function resolvedTheme(): Theme {
  return getStoredTheme() ?? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
}
