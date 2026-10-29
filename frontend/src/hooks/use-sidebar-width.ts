import { useEffect, useRef, useState, type PointerEvent, type KeyboardEvent } from 'react'
import { useLocalStorage } from '@/hooks/use-local-storage'

const DEFAULT = 248
const MIN = 200
const MAX = 360
const clamp = (value: number) => Math.max(MIN, Math.min(MAX, window.innerWidth - 480, value))

/** Save only completed gestures; viewport constraints never overwrite the preference. */
export function useSidebarWidth() {
  const [saved, setSaved] = useLocalStorage<number>('amd-ui-sidebar-width', DEFAULT)
  const [draft, setDraft] = useState<number | null>(null)
  const [viewport, setViewport] = useState(() => window.innerWidth)
  const drag = useRef<{ x: number; width: number; value: number } | null>(null)
  const preferred = Number.isFinite(saved) ? Math.max(MIN, Math.min(MAX, saved)) : DEFAULT
  const width = Math.max(MIN, Math.min(MAX, viewport - 480, draft ?? preferred))
  const finish = (save: boolean) => {
    if (!drag.current) return
    if (save) setSaved(drag.current.value)
    drag.current = null
    setDraft(null)
  }
  useEffect(() => {
    const resize = () => setViewport(window.innerWidth)
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [])
  const dragging = draft !== null
  useEffect(() => {
    if (!dragging) return
    const oldCursor = document.body.style.cursor
    const oldSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const cancel = () => { drag.current = null; setDraft(null) }
    window.addEventListener('blur', cancel)
    return () => {
      document.body.style.cursor = oldCursor
      document.body.style.userSelect = oldSelect
      window.removeEventListener('blur', cancel)
    }
  }, [dragging])
  return {
    width, dragging,
    separatorProps: {
      role: 'separator', tabIndex: 0, 'aria-orientation': 'vertical' as const,
      'aria-label': '调整侧栏宽度', 'aria-valuemin': MIN,
      'aria-valuemax': Math.max(MIN, Math.min(MAX, viewport - 480)), 'aria-valuenow': Math.round(width),
      onDoubleClick: () => setSaved(DEFAULT),
      onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
        if (event.button !== 0) return
        event.preventDefault()
        event.currentTarget.focus()
        event.currentTarget.setPointerCapture(event.pointerId)
        drag.current = { x: event.clientX, width, value: width }
        setDraft(width)
      },
      onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
        if (!drag.current) return
        const value = clamp(drag.current.width + event.clientX - drag.current.x)
        drag.current.value = value
        setDraft(value)
      },
      onPointerUp: (event: PointerEvent<HTMLDivElement>) => {
        if (drag.current) drag.current.value = clamp(drag.current.width + event.clientX - drag.current.x)
        finish(true)
      },
      onPointerCancel: () => finish(false),
      onLostPointerCapture: () => finish(false),
      onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
        if (event.key === 'Escape') { finish(false); return }
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault()
          setSaved(clamp(width + (event.key === 'ArrowLeft' ? -10 : 10)))
        } else if (event.key === 'Home' || event.key === 'Enter') {
          event.preventDefault(); setSaved(DEFAULT)
        }
      },
    },
  }
}
