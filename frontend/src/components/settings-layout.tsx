import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

type SettingsSection = {
  id: string
  label: string
  description: string
  content: ReactNode
}

/** One continuous page; navigation scrolls without unmounting settings or drafts. */
export function SettingsLayout({ title, description, sections, readOnly = false, children }: {
  title: string
  description: string
  sections: SettingsSection[]
  readOnly?: boolean
  children?: ReactNode
}) {
  const prefix = useId()
  const [active, setActive] = useState(sections[0].id)
  const rootRef = useRef<HTMLDivElement>(null)
  const navRef = useRef<HTMLElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const sectionRefs = useRef(new Map<string, HTMLElement>())
  const sectionIds = sections.map(section => section.id).join('|')
  const topOffset = () => 16

  useEffect(() => {
    const root = rootRef.current
    const scroller = contentRef.current
    if (!root || !scroller) return
    const ids = sectionIds.split('|')
    let frame = 0
    const update = () => {
      frame = 0
      const line = scroller.getBoundingClientRect().top + topOffset() + 2
      let current = ids[0]
      for (const id of ids) {
        const section = sectionRefs.current.get(id)
        if (section && section.getBoundingClientRect().top <= line) current = id
      }
      if (scroller.scrollTop > 0 && scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2) {
        current = ids[ids.length - 1]
      }
      setActive(current)
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update) }
    const observer = new ResizeObserver(schedule)
    observer.observe(root)
    if (scroller.firstElementChild) observer.observe(scroller.firstElementChild)
    const fieldset = scroller.querySelector('fieldset')
    if (fieldset) observer.observe(fieldset)
    scroller.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
    schedule()
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      scroller.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
    }
  }, [sectionIds])

  useEffect(() => {
    const nav = navRef.current
    const button = nav?.querySelector<HTMLElement>('[aria-current="true"]')
    if (!nav || !button || nav.scrollWidth <= nav.clientWidth) return
    const rect = button.getBoundingClientRect()
    const bounds = nav.getBoundingClientRect()
    if (rect.left < bounds.left || rect.right > bounds.right) {
      nav.scrollTo({ left: nav.scrollLeft + rect.left - bounds.left - (nav.clientWidth - rect.width) / 2 })
    }
  }, [active])

  const jumpTo = (id: string) => {
    const section = sectionRefs.current.get(id)
    const scroller = contentRef.current
    if (!section || !scroller) return
    scroller.scrollTo({
      top: scroller.scrollTop + section.getBoundingClientRect().top - scroller.getBoundingClientRect().top - topOffset(),
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
    })
  }
  return (
    <div ref={rootRef} className="mx-auto grid h-full min-h-0 w-full max-w-6xl grid-rows-[auto_minmax(0,1fr)] overflow-hidden lg:grid-cols-[220px_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)]">
      <aside className="flex min-h-0 min-w-0 flex-col gap-4 border-b p-4 lg:gap-6 lg:border-b-0 lg:border-r lg:px-5 lg:py-8">
        <header className="shrink-0">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-2 break-words text-sm text-muted-foreground">{description}</p>
        </header>
        <nav ref={navRef} aria-label={`${title}分类`} className="settings-index z-20 flex min-h-0 gap-1 overflow-auto rounded-xl border bg-background/95 p-1.5 backdrop-blur-md lg:flex-col">
          {sections.map(section => (
            <button
              key={section.id}
              id={`${prefix}-${section.id}-nav`}
              type="button"
              aria-controls={`${prefix}-${section.id}`}
              aria-current={active === section.id ? 'true' : undefined}
              onClick={() => jumpTo(section.id)}
              className={cn('shrink-0 rounded-lg px-3 py-2.5 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                active === section.id ? 'bg-background font-medium text-primary shadow-sm' : 'text-muted-foreground hover:bg-accent hover:text-foreground')}
            >{section.label}</button>
          ))}
        </nav>
      </aside>
      <div ref={contentRef} data-settings-content className="min-h-0 min-w-0 overflow-y-auto overscroll-contain p-4 md:p-8">
          {readOnly && <p className="mb-4 rounded-lg border bg-muted/20 p-3 text-sm text-muted-foreground">当前为只读权限。</p>}
          <fieldset disabled={readOnly} className="min-w-0 space-y-10">
            {sections.map(section => (
              <section key={section.id} ref={node => { if (node) sectionRefs.current.set(section.id, node); else sectionRefs.current.delete(section.id) }} id={`${prefix}-${section.id}`} aria-labelledby={`${prefix}-${section.id}-nav`} className="border-b border-border/60 pb-8 last:border-b-0 last:pb-0">
                <div className="mb-4">
                  <h2 className="text-lg font-semibold">{section.label}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{section.description}</p>
                </div>
                <div className="space-y-4">{section.content}</div>
              </section>
            ))}
            {children}
          </fieldset>
      </div>
    </div>
  )
}
