export const ACCENT_COLORS = [
  { key: 'graphite', label: '石墨', hex: '#35383d' },
  { key: 'blue', label: '雾蓝', hex: '#496b96' },
  { key: 'slate', label: '岩蓝', hex: '#536675' },
  { key: 'terracotta', label: '陶土橙', hex: '#b65f43' },
  { key: 'moss', label: '苔灰', hex: '#58725f' },
  { key: 'stone', label: '暖石', hex: '#786c60' },
] as const
export type AccentKey = typeof ACCENT_COLORS[number]['key']
export function normalizeAccent(value: unknown): AccentKey | null {
  return ACCENT_COLORS.find(c => c.key === value)?.key ?? null
}
