import { useCallback, useLayoutEffect, useRef, useState } from 'react'

/** Follow content growth until the reader scrolls up; scroll only the chat viewport. */
export function useChatScroll(sessionId: string | null, hasTurns: boolean) {
  const viewportRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const scheduleRef = useRef(() => {})
  const navigationPaused = useRef(false)
  const jumpRef = useRef<(id: string) => void>(() => {})
  const [showJump, setShowJump] = useState(false)

  const scrollToBottom = useCallback(() => {
    following.current = true
    navigationPaused.current = false
    setShowJump(false)
    scheduleRef.current()
  }, [])

  const jumpToTurn = useCallback((id: string) => jumpRef.current(id), [])

  useLayoutEffect(() => {
    const viewport = viewportRef.current
    const content = contentRef.current
    following.current = true
    navigationPaused.current = false
    setShowJump(false)
    if (!viewport || !content) return
    let frame = 0
    let lastTop = viewport.scrollTop
    let touchY: number | undefined
    const remaining = () => viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop
    const pause = () => { following.current = false; setShowJump(remaining() > 4) }
    const schedule = () => {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        // Keep message bounds aligned with the composer when native scrollbars take space.
        viewport.style.setProperty('--chat-scrollbar-width', `${viewport.offsetWidth - viewport.clientWidth}px`)
        if (following.current) {
          viewport.scrollTop = viewport.scrollHeight
          lastTop = viewport.scrollTop
        }
        setShowJump(!following.current && (navigationPaused.current || remaining() > 4))
      })
    }
    scheduleRef.current = schedule
    jumpRef.current = id => {
      const target = Array.from(content.querySelectorAll<HTMLElement>('[data-turn-id]')).find(el => el.dataset.turnId === id)
      if (!target) return
      following.current = false
      navigationPaused.current = true
      viewport.scrollTop += target.getBoundingClientRect().top - viewport.getBoundingClientRect().top - 16
      lastTop = viewport.scrollTop
      setShowJump(true)
    }
    const onScroll = () => {
      if (!navigationPaused.current && remaining() <= 4) {
        following.current = true
        setShowJump(false)
      } else if (viewport.scrollTop < lastTop - 1) {
        pause()
      }
      lastTop = viewport.scrollTop
    }
    const onWheel = (event: WheelEvent) => { navigationPaused.current = false; if (event.deltaY < 0) pause(); else if (remaining() <= 4) scrollToBottom() }
    const onTouchStart = (event: TouchEvent) => { navigationPaused.current = false; touchY = event.touches[0]?.clientY }
    const onTouchMove = (event: TouchEvent) => {
      const next = event.touches[0]?.clientY
      if (next !== undefined && touchY !== undefined && next > touchY) pause()
      touchY = next
    }
    const onKey = (event: KeyboardEvent) => {
      // Text editing keys belong to the input, not to scroll navigation.
      const target = event.target as HTMLElement
      if (target.closest('input,textarea,[contenteditable="true"]')) return
      navigationPaused.current = false
      if (['ArrowUp', 'PageUp', 'Home'].includes(event.key) || (event.key === ' ' && event.shiftKey)) pause()
    }
    const onPointerDown = () => { navigationPaused.current = false }
    viewport.addEventListener('pointerdown', onPointerDown)
    viewport.addEventListener('scroll', onScroll, { passive: true })
    viewport.addEventListener('wheel', onWheel, { passive: true })
    viewport.addEventListener('touchstart', onTouchStart, { passive: true })
    viewport.addEventListener('touchmove', onTouchMove, { passive: true })
    viewport.addEventListener('keydown', onKey)
    const observer = new ResizeObserver(schedule)
    observer.observe(content)
    observer.observe(viewport)
    schedule()
    return () => {
      observer.disconnect()
      cancelAnimationFrame(frame)
      scheduleRef.current = () => {}
      jumpRef.current = () => {}
      viewport.removeEventListener('pointerdown', onPointerDown)
      viewport.removeEventListener('scroll', onScroll)
      viewport.removeEventListener('wheel', onWheel)
      viewport.removeEventListener('touchstart', onTouchStart)
      viewport.removeEventListener('touchmove', onTouchMove)
      viewport.removeEventListener('keydown', onKey)
    }
  }, [sessionId, hasTurns, scrollToBottom])

  return { viewportRef, contentRef, showJump, scrollToBottom, jumpToTurn }
}
