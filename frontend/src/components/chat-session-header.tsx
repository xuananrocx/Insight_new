import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'

export function ChatSessionHeader({ knowledgeBase, children }: { knowledgeBase: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const panelId = useId()
  const clear = () => { clearTimeout(timer.current); timer.current = undefined }
  useEffect(() => () => clearTimeout(timer.current), [])
  const scheduleClose = () => {
    clear()
    timer.current = setTimeout(() => {
      const focused = document.activeElement
      if ((root.current?.contains(focused) && (focused !== trigger.current || focused?.matches(':focus-visible'))) || document.querySelector('[data-radix-popper-content-wrapper]')) return
      setOpen(false)
    }, 300)
  }
  return <div ref={root} className={`relative z-20 shrink-0 pl-2.5 pr-4 ${open ? 'border-b border-border/60 py-2' : 'h-0'}`} onPointerEnter={e => {
    if (e.pointerType !== 'mouse') return
    clear(); timer.current = setTimeout(() => setOpen(true), 350)
  }} onPointerLeave={e => { if (e.pointerType === 'mouse') scheduleClose() }} onBlur={scheduleClose} onFocus={clear} onKeyDown={e => {
    if (e.key === 'Escape' && !document.querySelector('[data-radix-popper-content-wrapper]')) {
      clear(); setOpen(false); trigger.current?.focus()
    }
  }}>
    {/* Keep the former header area available for hover while its contents are hidden. */}
    {!open && <div aria-hidden="true" className="absolute inset-x-0 top-0 h-11" />}
    <div className={`relative flex min-w-0 items-center gap-2 ${open ? '' : 'pointer-events-none'}`}>
      {open && <div id={panelId} className="min-w-0 flex-1">{children}</div>}
      <button ref={trigger} type="button" aria-expanded={open} aria-controls={panelId} aria-label={open ? '收起会话顶部' : '展开会话顶部'} title={open ? '收起会话顶部' : '展开会话顶部'} onClick={() => { clear(); setOpen(v => !v) }} className={`inline-flex shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent/30 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${open ? 'h-7 w-7' : 'pointer-events-auto h-5 max-w-[min(60vw,320px)] gap-1 px-1.5 text-[10px]'}`}>
        <ChevronDown aria-hidden="true" className={`h-3 w-3 transition-transform motion-reduce:transition-none ${open ? 'rotate-180' : ''}`} />
        {!open && <span className="min-w-0 truncate" title={knowledgeBase}>{knowledgeBase}</span>}
      </button>
    </div>
  </div>
}
