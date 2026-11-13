import { useState, type ReactNode } from 'react'
import { CircleHelp } from 'lucide-react'
import * as Tooltip from '@radix-ui/react-tooltip'

/** Keep label and help together; long labels wrap within the text column only. */
export function SettingsHelp({ children, title, label }: {
  children: ReactNode
  title?: ReactNode
  label?: string
}) {
  const [open, setOpen] = useState(false)
  return <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 align-middle">
    {title !== undefined && <span className="min-w-0 break-words">{title}</span>}
    <Tooltip.Provider delayDuration={200}><Tooltip.Root open={open} onOpenChange={setOpen}>
    <Tooltip.Trigger asChild>
      <span role="button" tabIndex={0} aria-label={label ?? (typeof title === 'string' ? `${title}说明` : '设置说明')} aria-expanded={open}
        className="pointer-events-auto inline-flex h-5 w-5 shrink-0 cursor-help items-center justify-center rounded-full text-muted-foreground/70 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onPointerDown={event => event.stopPropagation()}
        onClick={event => { event.preventDefault(); event.stopPropagation(); setOpen(value => !value) }}
        onKeyDown={event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault(); event.stopPropagation(); setOpen(value => !value)
          }
        }}>
        <CircleHelp aria-hidden="true" className="h-3.5 w-3.5" />
      </span>
    </Tooltip.Trigger>
    <Tooltip.Portal><Tooltip.Content side="top" sideOffset={6} collisionPadding={12}
      className="z-[150] max-h-[min(24rem,70vh)] max-w-[min(360px,calc(100vw-24px))] overflow-y-auto whitespace-pre-line break-words rounded-lg border bg-popover px-3 py-2.5 text-xs font-normal leading-relaxed text-popover-foreground shadow-md">
      {children}
      <Tooltip.Arrow className="fill-popover" />
    </Tooltip.Content></Tooltip.Portal>
  </Tooltip.Root></Tooltip.Provider>
  </span>
}
