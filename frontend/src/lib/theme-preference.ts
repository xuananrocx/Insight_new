export type ThemeKey = 'minimal' | 'stripe-light' | 'linear' | 'macos'
export function normalizeTheme(value: unknown): ThemeKey | null {
  if (value === 'stripe-dark') return 'minimal'
  return value === 'minimal' || value === 'stripe-light' || value === 'linear' || value === 'macos' ? value : null
}

// Claim the old browser-wide preference only once, rather than copying it to every account.
export function readThemePreference(storage: Storage, key: string, legacyKey: string): ThemeKey | null {
  if (key === legacyKey) return null
  const saved = storage.getItem(key)
  if (saved !== null) return normalizeTheme(saved)
  if (!storage.getItem(`${legacyKey}:migrated`)) {
    const legacy = normalizeTheme(storage.getItem(legacyKey))
    if (legacy) storage.setItem(key, legacy)
    storage.setItem(`${legacyKey}:migrated`, '1')
    return legacy
  }
  return null
}
