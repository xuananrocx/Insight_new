import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'

const lastFocused = new WeakMap<HTMLElement, HTMLElement>()
function focusReturnTarget() {
  if (document.activeElement instanceof HTMLElement && document.activeElement !== document.body) return document.activeElement
  // An async preview temporarily disables its trigger, which moves focus to body.
  const parent = Array.from(document.querySelectorAll<HTMLElement>('[data-management-dialog]')).at(-1)
  return parent ? lastFocused.get(parent) ?? parent : null
}

/** Shared shell for management forms, including nested permission previews. */
export function ManagementDialog({ title, onClose, busy = false, className, children }: {
  title: string
  onClose: () => void
  busy?: boolean
  className?: string
  children: ReactNode
}) {
  const opener = useRef(focusReturnTarget())
  const [position, setPosition] = useState<CSSProperties>()
  useLayoutEffect(() => {
    const content = document.querySelector<HTMLElement>('[data-workspace-content]')
    if (!content) return
    const update = () => {
      const bounds = content.getBoundingClientRect()
      setPosition({
        left: bounds.left + bounds.width / 2,
        top: bounds.top + bounds.height / 2,
        width: Math.max(0, bounds.width - 32),
        maxHeight: Math.max(0, Math.min(bounds.height - 32, window.innerHeight * 0.9)),
      })
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(content)
    window.addEventListener('resize', update)
    return () => { observer.disconnect(); window.removeEventListener('resize', update) }
  }, [])
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
    <DialogContent
      className={cn('flex max-h-[90dvh] w-[calc(100%-2rem)] max-w-3xl flex-col gap-4 overflow-hidden p-4 sm:p-6', className)}
      style={position}
      aria-describedby={undefined}
      data-management-dialog
      onFocusCapture={event => {
        if (event.target instanceof HTMLElement && event.currentTarget.contains(event.target)) lastFocused.set(event.currentTarget, event.target)
      }}
      onInteractOutside={event => { if (busy) event.preventDefault() }}
      onEscapeKeyDown={event => { if (busy) event.preventDefault() }}
      onCloseAutoFocus={event => {
        event.preventDefault()
        if (opener.current?.isConnected) opener.current.focus()
      }}
    >
      <div className="flex shrink-0 items-center justify-between gap-4 border-b pb-3">
        <DialogTitle className="min-w-0 break-words text-base leading-snug">{title}</DialogTitle>
        <Button type="button" size="icon" variant="ghost" className="h-8 w-8 shrink-0" aria-label="关闭弹窗" disabled={busy} onClick={onClose}><X className="h-4 w-4" /></Button>
      </div>
      <div className="min-h-0 min-w-0 space-y-4 overflow-y-auto overscroll-contain break-words pr-1">{children}</div>
    </DialogContent>
  </Dialog>
}
