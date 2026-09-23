import { useCallback, useEffect } from 'react'
import { useLocalStorage } from '@/hooks/use-local-storage'

export type BackgroundKey =
  | 'deep-blue'         // 深海蓝（默认）
  | 'smoky-blue'        // 烟熏蓝
  | 'moonlit-mint'      // Moonlit Mint
  | 'pearl-dusk'        // Pearl Dusk
  | 'emerald-vault'     // Emerald Vault
  | 'rose-gold-mist'    // Rose Gold Mist
  | 'aurora-haze'       // Aurora Haze
  | 'obsidian'          // Obsidian
  | 'galaxy-mesh'       // 星河 Mesh

export const BACKGROUND_CLASS_MAP: Record<BackgroundKey, string> = {
  'deep-blue': '',
  'smoky-blue': 'bg-smoky-blue',
  'moonlit-mint': 'bg-moonlit-mint',
  'pearl-dusk': 'bg-pearl-dusk',
  'emerald-vault': 'bg-emerald-vault',
  'rose-gold-mist': 'bg-rose-gold-mist',
  'aurora-haze': 'bg-aurora-haze',
  'obsidian': 'bg-obsidian',
  'galaxy-mesh': 'bg-galaxy-mesh',
}

/** 用于设置页色块预览的 mini 样式（CSS 字符串） */
export const BACKGROUND_PREVIEW_CSS: Record<BackgroundKey, string> = {
  'deep-blue': 'linear-gradient(135deg, #0a1628 0%, #1a2c4e 30%, #2c3e50 70%, #4a5568 100%)',
  'smoky-blue': 'linear-gradient(135deg, #1c2a3a 0%, #2d3e50 33%, #3e5266 66%, #4f6580 100%)',
  'moonlit-mint': 'radial-gradient(ellipse 80% 60% at 50% 0%, rgba(94,234,212,0.15) 0%, transparent 60%), #0a141a',
  'pearl-dusk': 'radial-gradient(ellipse 80% 60% at 50% 0%, rgba(252,211,77,0.12) 0%, transparent 60%), #1a1612',
  'emerald-vault': 'radial-gradient(ellipse 80% 60% at 50% 0%, rgba(16,185,129,0.20) 0%, transparent 60%), radial-gradient(ellipse 50% 40% at 80% 80%, rgba(252,211,77,0.10) 0%, transparent 50%), #0a1410',
  'rose-gold-mist': 'radial-gradient(ellipse 80% 60% at 50% 0%, rgba(251,113,133,0.18) 0%, transparent 60%), radial-gradient(ellipse 60% 50% at 75% 85%, rgba(252,211,77,0.08) 0%, transparent 55%), #1a0a14',
  'aurora-haze': 'radial-gradient(ellipse 70% 50% at 15% 25%, rgba(120,119,198,0.20) 0%, transparent 55%), radial-gradient(ellipse 70% 50% at 85% 15%, rgba(236,72,153,0.12) 0%, transparent 50%), radial-gradient(ellipse 80% 60% at 50% 90%, rgba(34,211,238,0.15) 0%, transparent 55%), #0a0e1a',
  'obsidian': 'radial-gradient(ellipse 60% 50% at 25% 15%, rgba(251,191,36,0.07) 0%, transparent 55%), radial-gradient(ellipse 70% 60% at 75% 85%, rgba(59,130,246,0.06) 0%, transparent 55%), #050507',
  'galaxy-mesh': 'radial-gradient(at 15% 20%, rgba(56,189,248,0.5) 0%, transparent 50%), radial-gradient(at 80% 20%, rgba(129,140,248,0.5) 0%, transparent 50%), radial-gradient(at 60% 80%, rgba(167,139,250,0.4) 0%, transparent 50%), #0f172a',
}

export const BACKGROUND_OPTIONS: Array<{
  key: BackgroundKey
  label: string
  description: string
}> = [
  { key: 'deep-blue', label: '深海蓝', description: '沉稳商务' },
  { key: 'smoky-blue', label: '烟熏蓝', description: '克制蓝灰单色阶' },
  { key: 'moonlit-mint', label: 'Moonlit Mint', description: '深青底 + 月光薄荷光晕 · 默认' },
  { key: 'pearl-dusk', label: 'Pearl Dusk', description: '暖灰底 + 珍珠金光晕' },
  { key: 'emerald-vault', label: 'Emerald Vault', description: '深翡翠 + 角落金光（Rolex 风）' },
  { key: 'rose-gold-mist', label: 'Rose Gold Mist', description: '深酒红 + 玫瑰金（Hermès 风）' },
  { key: 'aurora-haze', label: 'Aurora Haze', description: '深空 + 紫/粉/青薄雾' },
  { key: 'obsidian', label: 'Obsidian', description: '近纯黑 + 暖冷双光' },
  { key: 'galaxy-mesh', label: '星河 Mesh', description: '蓝紫青多色辐射' },
]

const STORAGE_KEY = 'amd-ui-background'

export function useBackground() {
  const [background, setBackground] = useLocalStorage<BackgroundKey>(
    STORAGE_KEY,
    'moonlit-mint',
  )

  /** 应用 class 到 documentElement */
  const apply = useCallback((key: BackgroundKey) => {
    const root = window.document.documentElement
    // 清掉所有 background modifier class
    root.classList.remove(
      'bg-smoky-blue',
      'bg-moonlit-mint',
      'bg-pearl-dusk',
      'bg-emerald-vault',
      'bg-rose-gold-mist',
      'bg-aurora-haze',
      'bg-obsidian',
      'bg-galaxy-mesh',
    )
    const cls = BACKGROUND_CLASS_MAP[key]
    if (cls) root.classList.add(cls)
  }, [])

  const set = useCallback(
    (key: BackgroundKey) => {
      setBackground(key)
      apply(key)
    },
    [setBackground, apply],
  )

  // mount 时应用一次初始值，防止早期脚本未生效或用户从其他 tab 切回时 class 丢失
  useEffect(() => {
    apply(background)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [background, apply])

  return { background, set }
}
