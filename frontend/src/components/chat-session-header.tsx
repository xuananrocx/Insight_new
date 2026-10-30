import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { ChevronDown, Pin } from 'lucide-react'
import { useLocalStorage } from '@/hooks/use-local-storage'

export function ChatSessionHeader({ knowledgeBase, children }: { knowledgeBase: string; children: ReactNode }) {
  const [autoExpand] = useLocalStorage('amd-chat-header-auto-expand', true)
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const pinButton = useRef<HTMLButtonElement>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const panelId = useId()
  const clear = () => { clearTimeout(timer.current); timer.current = undefined }
  useEffect(() => {
    clearTimeout(timer.current)
    return () => clearTimeout(timer.current)
  }, [autoExpand])
  const scheduleClose = () => {
    clear()
    timer.current = setTimeout(() => {
      const focused = document.activeElement
      if (pinned || (root.current?.contains(focused) && ((focused !== trigger.current && focused !== pinButton.current) || focused?.matches(':focus-visible'))) || document.querySelector('[data-radix-popper-content-wrapper]')) return
      setOpen(false)
    }, 300)
  }
  return <div ref={root} className={`relative z-20 shrink-0 pl-2.5 pr-4 ${open ? 'border-b border-border/60 py-2' : 'h-0'}`} onPointerEnter={e => {
    if (e.pointerType !== 'mouse' || !autoExpand) return
    clear(); timer.current = setTimeout(() => setOpen(true), 350)
  }} onPointerLeave={e => { if (e.pointerType === 'mouse') scheduleClose() }} onBlur={scheduleClose} onFocus={clear} onKeyDown={e => {
    if (e.key === 'Escape' && !document.querySelector('[data-radix-popper-content-wrapper]')) {
      clear(); setPinned(false); setOpen(false); trigger.current?.focus()
    }
  }}>
    {/* Keep the former header area available for hover while its contents are hidden. */}
    {!open && autoExpand && <div aria-hidden="true" className="absolute inset-x-0 top-0 h-11" />}
    <div className={`relative flex min-w-0 flex-wrap items-center gap-2 ${open ? '' : 'pointer-events-none'}`}>
      {open && <div id={panelId} className="min-w-0 flex-1">{children}</div>}
      {open && <button ref={pinButton} type="button" aria-label={pinned ? '取消固定会话顶部' : '固定展开会话顶部'} title={pinned ? '取消固定' : '固定展开'} aria-pressed={pinned} onClick={() => { clear(); setPinned(v => !v) }} className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded hover:bg-accent/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${pinned ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground'}`}>
        <Pin aria-hidden="true" className="h-3.5 w-3.5" />
      </button>}
      <button ref={trigger} type="button" aria-expanded={open} aria-controls={panelId} aria-label={open ? '收起会话顶部' : '展开会话顶部'} title={open ? '收起会话顶部' : '展开会话顶部'} onClick={() => { clear(); setPinned(false); setOpen(v => !v) }} className={`inline-flex shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent/30 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${open ? 'h-7 w-7' : 'pointer-events-auto h-5 max-w-[min(60vw,320px)] gap-1 px-1.5 text-[10px]'}`}>
        <ChevronDown aria-hidden="true" className={`h-3 w-3 transition-transform motion-reduce:transition-none ${open ? 'rotate-180' : ''}`} />
        {!open && <span className="min-w-0 truncate" title={knowledgeBase}>{knowledgeBase}</span>}
      </button>
    </div>
  </div>
}
