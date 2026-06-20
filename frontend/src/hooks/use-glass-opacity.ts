import { useEffect } from 'react'
import { useLocalStorage } from './use-local-storage'

/**
 * 控制 macOS 毛玻璃主题的透明度。
 * 通过设置 :root 的 --glass-alpha CSS 变量实时生效。
 */
export function useGlassOpacity() {
  const [opacity, setOpacity] = useLocalStorage('amd-ui-glass-opacity', 0.30)

  useEffect(() => {
    const root = document.documentElement
    root.style.setProperty('--glass-alpha', String(opacity))
  }, [opacity])

  return [opacity, setOpacity] as const
}
