import { useEffect, useRef, useState } from 'react'
import { Palette, Check } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useTheme, type ThemeKey } from '@/hooks/use-theme'
import { cn } from '@/lib/utils'

const THEMES: { key: ThemeKey; label: string; preview: string[] }[] = [
  {
    key: 'stripe-light',
    label: 'Stripe 浅色',
    preview: ['#f6f9fc', '#635bff', '#e3e8ee'],
  },
  {
    key: 'stripe-dark',
    label: 'Stripe 深色',
    preview: ['#0f1729', '#7c6fff', '#1a2238'],
  },
  {
    key: 'linear',
    label: 'Linear 紫黑',
    preview: ['#101016', '#5e6ad2', '#1c1d24'],
  },
  {
    key: 'macos',
    label: 'macOS 毛玻璃',
    preview: ['#0a1628', '#1c75c9', '#354159'],
  },
]

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    if (open) {
      document.addEventListener('mousedown', onClick)
      return () => document.removeEventListener('mousedown', onClick)
    }
  }, [open])

  const current = THEMES.find((t) => t.key === theme) ?? THEMES[0]

  return (
    <div ref={ref} className="relative">
      <Button
        variant="ghost"
        size="sm"
        className="gap-1.5 px-2 text-[12px]"
        onClick={() => setOpen(!open)}
        title="切换主题"
      >
        <Palette className="h-3.5 w-3.5" />
        <div className="flex gap-0.5">
          {current.preview.map((c, i) => (
            <span
              key={i}
              className="h-3 w-3 rounded-full border border-border/50"
              style={{ background: c }}
            />
          ))}
        </div>
      </Button>

      {open ? (
        <div className="absolute right-0 top-full z-50 mt-1 w-48 overflow-hidden rounded-lg border bg-popover p-1 shadow-md">
          <div className="px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            主题
          </div>
          {THEMES.map((t) => (
            <button
              key={t.key}
              onClick={() => {
                setTheme(t.key)
                setOpen(false)
              }}
              className={cn(
                'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[12px] transition-colors hover:bg-accent',
                theme === t.key && 'bg-accent/60',
              )}
            >
              <div className="flex gap-0.5">
                {t.preview.map((c, i) => (
                  <span
                    key={i}
                    className="h-3.5 w-3.5 rounded-full border border-border/50"
                    style={{ background: c }}
                  />
                ))}
              </div>
              <span className="flex-1 text-left">{t.label}</span>
              {theme === t.key ? <Check className="h-3 w-3 text-primary" /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
