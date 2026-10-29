import { createContext, useContext, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { accountApi, accountKey } from '@/lib/account-api'
import { normalizeTheme, readThemePreference, type ThemeKey } from '@/lib/theme-preference'
import { normalizeAccent, type AccentKey } from '@/lib/accent-colors'
export type { ThemeKey } from '@/lib/theme-preference'

type ThemeProviderState = {
  accent: AccentKey
  setAccent: (key: AccentKey) => void
  resetAccent: () => void
  followsDefaultAccent: boolean
  theme: ThemeKey
  setTheme: (theme: ThemeKey) => void
  followsDefault: boolean
  resetTheme: () => void
}
const ThemeProviderContext = createContext<ThemeProviderState | undefined>(undefined)

export function ThemeProvider({ children, defaultTheme = 'minimal', storageKey = 'amd-ui-theme' }: {
  children: React.ReactNode; defaultTheme?: ThemeKey; storageKey?: string
}) {
  const [key, setKey] = useState(() => accountKey(storageKey))
  const read = (currentKey: string) => {
    try { return readThemePreference(localStorage, currentKey, storageKey) } catch { return null }
  }
  const readAccent = () => { try { return normalizeAccent(localStorage.getItem(accountKey('amd-ui-accent'))) } catch { return null } }
  const [accentPreference, setAccentPreference] = useState<AccentKey | null>(readAccent)
  const [preference, setPreference] = useState<ThemeKey | null>(() => read(key))
  const { data } = useQuery({ queryKey: ['appearance-default'], queryFn: accountApi.status, staleTime: 30_000, refetchOnWindowFocus: true })
  const accent = accentPreference ?? normalizeAccent(data?.default_accent) ?? 'graphite'
  const theme = preference ?? normalizeTheme(data?.default_theme) ?? defaultTheme

  useEffect(() => {
    const sync = () => {
      const currentKey = accountKey(storageKey)
      setAccentPreference(readAccent())
      setKey(currentKey)
      setPreference(read(currentKey))
    }
    sync()
    window.addEventListener('insight-theme-account-change', sync)
    window.addEventListener('storage', sync)
    return () => {
      window.removeEventListener('insight-theme-account-change', sync)
      window.removeEventListener('storage', sync)
    }
    // Storage identity changes on sign-in; defaults must never become personal preferences.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey])

  useEffect(() => {
    const root = document.documentElement
    root.classList.remove('theme-minimal', 'theme-stripe-light', 'theme-stripe-dark', 'theme-linear', 'theme-macos')
    root.classList.add(`theme-${theme}`)
    root.dataset.accent = accent
    try { localStorage.setItem('amd-ui-theme-rendered', theme) } catch { /* Cosmetic cache only. */ }
  }, [theme, accent])

  const setTheme = (next: ThemeKey) => {
    try { localStorage.setItem(key, next) } catch { /* Keep usable without storage. */ }
    setPreference(next)
  }
  const resetTheme = () => {
    // Explicit inheritance prevents the legacy preference from being imported again.
    try { localStorage.setItem(key, 'system') } catch { /* Keep usable without storage. */ }
    setPreference(null)
  }
  const setAccent = (next: AccentKey) => {
    try { localStorage.setItem(accountKey('amd-ui-accent'), next) } catch { /* Storage may be unavailable. */ }
    setAccentPreference(next)
  }
  const resetAccent = () => {
    try { localStorage.removeItem(accountKey('amd-ui-accent')) } catch { /* Storage may be unavailable. */ }
    setAccentPreference(null)
  }
  return <ThemeProviderContext.Provider value={{ accent, setAccent, resetAccent, followsDefaultAccent: accentPreference === null, theme, setTheme, followsDefault: preference === null, resetTheme }}>{children}</ThemeProviderContext.Provider>
}

// The provider and its context hook intentionally share one module.
// eslint-disable-next-line react-refresh/only-export-components
export const useTheme = () => {
  const context = useContext(ThemeProviderContext)
  if (!context) throw new Error('useTheme must be used within a ThemeProvider')
  return context
}
