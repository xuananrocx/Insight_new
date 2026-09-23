import { useLayoutEffect, useRef, type TextareaHTMLAttributes } from 'react'

export function AutosizeTextarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const element = ref.current!
    const resize = () => {
      const style = getComputedStyle(element)
      const line = parseFloat(style.lineHeight) || 20
      const maximum = Math.max(line * 2, Math.min(line * 8, window.innerHeight / 3))
      element.style.height = 'auto'
      element.style.height = `${Math.min(element.scrollHeight, maximum)}px`
      element.style.overflowY = element.scrollHeight > maximum ? 'auto' : 'hidden'
    }
    resize()
    let width = element.clientWidth
    const observer = new ResizeObserver(() => {
      if (width !== element.clientWidth) { width = element.clientWidth; resize() }
    })
    observer.observe(element)
    window.addEventListener('resize', resize)
    return () => { observer.disconnect(); window.removeEventListener('resize', resize) }
  }, [props.value])
  return <textarea {...props} ref={ref} />
}
