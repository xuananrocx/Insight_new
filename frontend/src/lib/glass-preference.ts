export const BACKGROUND_KEYS = ['smoky-blue', 'moonlit-mint', 'rose-gold-mist', 'aurora-haze', 'obsidian'] as const
export type BackgroundKey = typeof BACKGROUND_KEYS[number]
export function normalizeBackground(value: unknown): BackgroundKey {
  return BACKGROUND_KEYS.includes(value as BackgroundKey) ? value as BackgroundKey : 'moonlit-mint'
}
export function normalizeGlass(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(1, Math.max(.3, value)) : .6
}
export function readAppearanceValue(storage: Pick<Storage, 'getItem'>, key: string): unknown {
  try { const raw = storage.getItem(key); return raw === null ? null : JSON.parse(raw) } catch { return null }
}
