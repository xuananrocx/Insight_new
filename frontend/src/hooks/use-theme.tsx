import { createContext, useContext, useEffect, useState } from 'react'

export type ThemeKey = 'stripe-light' | 'stripe-dark' | 'linear' | 'macos'

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
  'stripe-light': '',
  'stripe-dark': 'theme-stripe-dark',
  'linear': 'theme-linear',
  'macos': 'theme-macos',
}

const initialState: ThemeProviderState = {
  theme: 'stripe-light',
  setTheme: () => null,
}

const ThemeProviderContext = createContext<ThemeProviderState>(initialState)

export function ThemeProvider({
  children,
  defaultTheme = 'stripe-light',
  storageKey = 'amd-ui-theme',
  ...props
}: ThemeProviderProps) {
  const [theme, setThemeState] = useState<ThemeKey>(
    () => (localStorage.getItem(storageKey) as ThemeKey) || defaultTheme,
  )

  useEffect(() => {
    const root = window.document.documentElement
    root.classList.remove('theme-stripe-dark', 'theme-linear', 'theme-macos')
    const cls = THEME_CLASS_MAP[theme]
    if (cls) {
      root.classList.add(cls)
    }
    localStorage.setItem(storageKey, theme)
  }, [theme])

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
