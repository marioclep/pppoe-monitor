export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'pppoe_theme'

export function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null
  }
}

export function systemTheme(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function saveTheme(theme: Theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* not persisted: the next visit follows the system theme */
  }
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
}
