import { useEffect } from 'react'
import { useAppearancePreference } from './use-appearance-preference'
import { normalizeGlass } from '@/lib/glass-preference'

/**
 * 控制 macOS 毛玻璃主题的透明度。
 * 通过设置 :root 的 --glass-alpha CSS 变量实时生效。
 */
export function useGlassOpacity() {
  const [opacity, setOpacity] = useAppearancePreference('amd-ui-glass-opacity', normalizeGlass)

  useEffect(() => {
    const root = document.documentElement
    root.style.setProperty('--glass-alpha', String(opacity))
    try { localStorage.setItem('amd-ui-glass-rendered', String(opacity)) } catch { /* Cosmetic cache only. */ }
  }, [opacity])

  return [opacity, setOpacity] as const
}
