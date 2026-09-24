import { useEffect, useRef, useState, type RefObject } from 'react'
import * as Tooltip from '@radix-ui/react-tooltip'
import { List } from 'lucide-react'
import type { ChatTurn } from '@/hooks/use-chat-sessions'
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

type Props = {
  turns: ChatTurn[]
  viewportRef: RefObject<HTMLDivElement | null>
  contentRef: RefObject<HTMLDivElement | null>
  onJump: (id: string) => void
}

export function MessageNavigation({ turns, viewportRef, contentRef, onJump }: Props) {
  const [active, setActive] = useState('')
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [left, setLeft] = useState(24)
  const openList = () => { setQuery(''); setOpen(true) }
  const railRef = useRef<HTMLElement>(null)
  const flashRef = useRef<Animation | null>(null)
  const turnIds = turns.map(turn => turn.id).join('\0')

  useEffect(() => {
    const viewport = viewportRef.current
    const content = contentRef.current
    if (!viewport || !content) return
    let frame = 0
    const update = () => {
      frame = 0
      const items = Array.from(content.querySelectorAll<HTMLElement>('[data-turn-id]'))
      const bounds = viewport.getBoundingClientRect()
      setLeft(Math.max(0, content.getBoundingClientRect().left - bounds.left - 78))
      const line = bounds.top + 32
      let current: HTMLElement | undefined = items[0]
      for (const item of items) {
        if (item.getBoundingClientRect().top <= line) current = item
        else break
      }
      if (viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= 4) current = items.at(-1)
      setActive(current?.dataset.turnId ?? '')
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update) }
    viewport.addEventListener('scroll', schedule, { passive: true })
    const observer = new ResizeObserver(schedule)
    observer.observe(content)
    observer.observe(viewport)
    schedule()
    return () => { observer.disconnect(); viewport.removeEventListener('scroll', schedule); cancelAnimationFrame(frame) }
  }, [turnIds, viewportRef, contentRef])

  useEffect(() => {
    const rail = railRef.current
    const button = rail?.querySelector<HTMLElement>('[aria-current="step"]')
    if (!rail || !button || rail.matches(':hover') || rail.contains(document.activeElement)) return
    const outer = rail.getBoundingClientRect(), inner = button.getBoundingClientRect()
    if (inner.top < outer.top + 12) rail.scrollTop -= outer.top + 12 - inner.top
    else if (inner.bottom > outer.bottom - 12) rail.scrollTop += inner.bottom - outer.bottom + 12
  }, [active])

  useEffect(() => () => { flashRef.current?.cancel() }, [])

  function jump(id: string) {
    onJump(id)
    setActive(id)
    setOpen(false)
    flashRef.current?.cancel()
    const target = Array.from(contentRef.current?.querySelectorAll<HTMLElement>('[data-turn-id]') ?? []).find(item => item.dataset.turnId === id)?.firstElementChild
    if (target && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      flashRef.current = target.animate([
        { boxShadow: 'inset 0 0 0 2px hsl(var(--primary) / 0.65)' },
        { boxShadow: 'inset 0 0 0 2px hsl(var(--primary) / 0)' },
      ], { duration: 1000 })
    }
  }

  if (turns.length < 2) return null
  return <>
    <div style={{ left }} className="pointer-events-none absolute inset-y-4 z-20 hidden w-7 flex-col items-center justify-center md:flex">
      <Tooltip.Provider delayDuration={180}>
        <nav ref={railRef} aria-label="消息导航" className="pointer-events-auto min-h-0 shrink overflow-y-auto overscroll-contain py-3 [scrollbar-width:none] [mask-image:linear-gradient(to_bottom,transparent,black_12px,black_calc(100%-12px),transparent)]" onKeyDown={event => {
          const buttons = Array.from(railRef.current?.querySelectorAll('button') ?? [])
          const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
          let next = index
          if (event.key === 'ArrowDown') next = Math.min(index + 1, buttons.length - 1)
          else if (event.key === 'ArrowUp') next = Math.max(index - 1, 0)
          else if (event.key === 'Home') next = 0
          else if (event.key === 'End') next = buttons.length - 1
          else return
          event.preventDefault(); buttons[next]?.focus({ preventScroll: true })
          const button = buttons[next], rail = railRef.current
          if (button && rail) {
            const a = rail.getBoundingClientRect(), b = button.getBoundingClientRect()
            if (b.top < a.top + 12) rail.scrollTop -= a.top + 12 - b.top
            if (b.bottom > a.bottom - 12) rail.scrollTop += b.bottom - a.bottom + 12
          }
        }}>
          {turns.map((turn, index) => <Tooltip.Root key={turn.id}>
            <Tooltip.Trigger asChild>
              <button type="button" aria-label={`第 ${index + 1} 轮：${turn.question}`} aria-current={active === turn.id ? 'step' : undefined}
                className="group flex h-5 w-7 shrink-0 items-center justify-center rounded outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary" onClick={() => jump(turn.id)}>
                <span className={`block h-px shrink-0 rounded-full group-hover:w-[18px] group-focus-visible:w-[18px] transition-[width,background-color] motion-reduce:transition-none ${active === turn.id ? 'w-3.5 bg-primary' : 'w-2 bg-muted-foreground/35 group-hover:bg-primary/70'} ${turn.thinking?.status === 'streaming' ? 'motion-safe:animate-pulse' : ''}`} />
              </button>
            </Tooltip.Trigger>
            <Tooltip.Portal><Tooltip.Content side="right" sideOffset={8} collisionPadding={12} className="z-50 w-72 max-w-[calc(100vw-32px)] rounded-lg border bg-popover p-3 text-popover-foreground shadow-lg">
              <p className="mb-1 text-[10px] text-muted-foreground">第 {index + 1} 轮{turn.thinking?.status === 'streaming' ? ' · 生成中' : ''}</p>
              <p className="line-clamp-6 whitespace-pre-wrap text-xs leading-relaxed [overflow-wrap:anywhere]">{turn.question}</p>
            </Tooltip.Content></Tooltip.Portal>
          </Tooltip.Root>)}
        </nav>
      </Tooltip.Provider>
      <Button type="button" size="icon" variant="ghost" aria-label="打开问题列表" title="问题列表" className="pointer-events-auto mt-1 h-7 w-7 shrink-0 text-muted-foreground" onClick={openList}><List className="h-3.5 w-3.5" /></Button>
    </div>
    <Button type="button" size="sm" variant="outline" className="absolute left-3 top-2 z-20 h-7 gap-1 bg-popover text-[11px] md:hidden" onClick={openList}><List className="h-3 w-3" />消息导航</Button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent>
      <DialogTitle>消息导航</DialogTitle><DialogDescription className="mt-2">选择问题，跳转到对应问答。</DialogDescription>
      <input type="search" className="mt-3 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="筛选问题" placeholder="输入关键词筛选问题…" value={query} onChange={event => setQuery(event.target.value)} />
      <div className="mt-3 max-h-[60vh] space-y-1 overflow-y-auto overscroll-contain">
        {turns.map((turn, index) => ({ turn, index })).filter(({ turn }) => turn.question.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())).map(({ turn, index }) => <button key={turn.id} type="button" aria-current={active === turn.id ? 'step' : undefined}
          className={`block w-full rounded-md p-2 text-left text-xs hover:bg-accent ${active === turn.id ? 'bg-accent' : ''}`} onClick={() => jump(turn.id)}>
          <span className="mb-1 block text-[10px] text-muted-foreground">第 {index + 1} 轮</span><span className="line-clamp-3 [overflow-wrap:anywhere]">{turn.question}</span>
        </button>)}
        {!turns.some(turn => turn.question.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) && <p className="py-6 text-center text-sm text-muted-foreground">没有匹配的问题</p>}
      </div>
    </DialogContent></Dialog>
  </>
}
