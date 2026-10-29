import { createContext, useContext, useEffect, useState } from 'react'

export type ThemeKey = 'minimal' | 'stripe-light' | 'linear' | 'macos'

type ThemeProviderProps = {
  children: React.ReactNode
  defaultTheme?: ThemeKey
  storageKey?: string
}

type ThemeProviderState = {
  theme: ThemeKey
  setTheme: (theme: ThemeKey) => void
}

const THEME_CLASS_MAP: Record<ThemeKey, string> = {
  'minimal': 'theme-minimal',
  'stripe-light': 'theme-stripe-light',
  'linear': 'theme-linear',
  'macos': 'theme-macos',
}

const initialState: ThemeProviderState = {
  theme: 'minimal',
  setTheme: () => null,
}

const ThemeProviderContext = createContext<ThemeProviderState>(initialState)

export function ThemeProvider({
  children,
  defaultTheme = 'minimal',
  storageKey = 'amd-ui-theme',
  ...props
}: ThemeProviderProps) {
  const [theme, setThemeState] = useState<ThemeKey>(
    () => {
      try {
        const saved = localStorage.getItem(storageKey)
        // The retired Stripe dark theme migrates to the neutral theme.
        if (saved === 'stripe-dark') return 'minimal'
        return saved === 'minimal' || saved === 'stripe-light' || saved === 'linear' || saved === 'macos' ? saved : defaultTheme
      } catch { return defaultTheme }
    },
  )

  useEffect(() => {
    const root = window.document.documentElement
    root.classList.remove('theme-minimal', 'theme-stripe-light', 'theme-stripe-dark', 'theme-linear', 'theme-macos')
    const cls = THEME_CLASS_MAP[theme]
    if (cls) {
      root.classList.add(cls)
    }
    try { localStorage.setItem(storageKey, theme) } catch { /* Keep theme usable when storage is unavailable. */ }
  }, [theme, storageKey])

  const value = {
    theme,
    setTheme: setThemeState,
  }

  return (
    <ThemeProviderContext.Provider {...props} value={value}>
      {children}
    </ThemeProviderContext.Provider>
  )
}

export const useTheme = () => {
  const context = useContext(ThemeProviderContext)
  if (context === undefined)
    throw new Error('useTheme must be used within a ThemeProvider')
  return context
}
