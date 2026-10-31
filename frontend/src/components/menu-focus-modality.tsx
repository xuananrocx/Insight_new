import { useEffect } from 'react'

// Radix restores focus to menu triggers on close. Keep that behavior, but
// distinguish pointer use from keyboard use when drawing the focus indicator.
export function MenuFocusModality() {
  useEffect(() => {
    const root = document.documentElement
    const pointer = () => { root.dataset.menuFocusInput = 'pointer' }
    const keyboard = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || ['Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) return
      root.dataset.menuFocusInput = 'keyboard'
    }
    document.addEventListener('pointerdown', pointer, true)
    document.addEventListener('keydown', keyboard, true)
    return () => {
      document.removeEventListener('pointerdown', pointer, true)
      document.removeEventListener('keydown', keyboard, true)
      delete root.dataset.menuFocusInput
    }
  }, [])
  return null
}
