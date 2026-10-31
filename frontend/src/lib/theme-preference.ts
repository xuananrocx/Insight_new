export type ThemeKey = 'minimal' | 'everforest' | 'macos'
export function normalizeTheme(value: unknown): ThemeKey | null {
  if (value === 'linear') return 'everforest'
  if (value === 'stripe-light' || value === 'stripe-dark') return 'minimal'
  return value === 'minimal' || value === 'everforest' || value === 'macos' ? value : null
}

// Claim the old browser-wide preference only once, rather than copying it to every account.
export function readThemePreference(storage: Storage, key: string, legacyKey: string, accentKey?: string): ThemeKey | null {
  if (key === legacyKey) return null
  const migrate = (raw: string | null) => {
    if (raw === 'stripe-light' && accentKey) {
      storage.setItem(accentKey, 'moss')
      storage.setItem(key, 'minimal')
    }
    return normalizeTheme(raw)
  }
  const saved = storage.getItem(key)
  if (saved !== null) return migrate(saved)
  if (!storage.getItem(`${legacyKey}:migrated`)) {
    const legacy = migrate(storage.getItem(legacyKey))
    if (legacy) storage.setItem(key, legacy)
    storage.setItem(`${legacyKey}:migrated`, '1')
    return legacy
  }
  return null
}
