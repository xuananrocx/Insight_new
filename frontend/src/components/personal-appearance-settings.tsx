import { useAuth } from '@/hooks/use-auth'
import { DEFAULT_MENU_ORDER, MENU_LABELS, normalizeMenuOrder, visibleMenuOrder } from '@/lib/navigation'
import { Palette, PanelLeft, ChevronUp, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ThemeToggle } from '@/components/theme-toggle'
import { useTheme } from '@/hooks/use-theme'
import { useBackground, BACKGROUND_OPTIONS, BACKGROUND_PREVIEW_CSS } from '@/hooks/use-background'
import { useGlassOpacity } from '@/hooks/use-glass-opacity'
import { useLocalStorage } from '@/hooks/use-local-storage'

export function SidebarSettingsCard() {
  const { can } = useAuth()
  const [showLogs] = useLocalStorage('amd-ui-show-logs', false)
  const [menuOrder, setMenuOrder] = useLocalStorage<string[]>(
    'amd-ui-menu-order',
    DEFAULT_MENU_ORDER,
  )

  const visibleOrder = visibleMenuOrder(menuOrder, can, showLogs)
  const move = (idx: number, dir: 'up' | 'down') => {
    const target = dir === 'up' ? idx - 1 : idx + 1
    if (target < 0 || target >= visibleOrder.length) return
    const next = normalizeMenuOrder(menuOrder)
    const fromIndex = next.indexOf(visibleOrder[idx])
    const toIndex = next.indexOf(visibleOrder[target])
    ;[next[fromIndex], next[toIndex]] = [next[toIndex], next[fromIndex]]
    setMenuOrder(next)
  }

  const reset = () => setMenuOrder(DEFAULT_MENU_ORDER)

  return (
    <Card className="mb-4 p-5">
      <div className="mb-3 flex items-center gap-2">
        <PanelLeft className="h-4 w-4 text-muted-foreground" />
        <span className="text-[14px] font-medium">侧边栏</span>
      </div>
      <p className="mb-3 text-[12px] text-muted-foreground">
        仅展示当前可见的主菜单，调整其显示顺序。改动立即生效，关闭浏览器后仍保留。
      </p>
      <div className="space-y-1">
        {visibleOrder.map((path, idx) => {
          const label = MENU_LABELS[path] ?? path
          return (
            <div
              key={path}
              className="flex items-center justify-between rounded-md border bg-background px-3 py-2 text-[13px]"
            >
              <span className="flex items-center gap-2">
                <span className="text-muted-foreground">{idx + 1}.</span>
                {label}
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => move(idx, 'up')}
                  disabled={idx === 0}
                  className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-30"
                  title="上移"
                >
                  <ChevronUp className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => move(idx, 'down')}
                  disabled={idx === visibleOrder.length - 1}
                  className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-30"
                  title="下移"
                >
                  <ChevronDown className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-3 flex justify-end">
        <Button variant="outline" size="sm" onClick={reset} className="text-[12px]">
          恢复默认顺序
        </Button>
      </div>
    </Card>
  )
}

export function AppearanceSettingsCard() {
  const { theme } = useTheme()
  const { background, set: setBackground } = useBackground()
  const [glassOpacity, setGlassOpacity] = useGlassOpacity()
  return (
      <Card className="mb-4 p-5">
        <div className="mb-3 flex items-center gap-2">
          <Palette className="h-4 w-4 text-muted-foreground" />
          <span className="text-[14px] font-medium">外观</span>
        </div>

        <div className="mb-4 flex items-center justify-between gap-3 rounded-md border p-3">
          <span className="text-sm">主题</span>
          <ThemeToggle />
        </div>
        {theme === 'macos' ? (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <label className="text-[12px] text-muted-foreground">毛玻璃透明度</label>
              <span className="font-mono text-[11px] text-muted-foreground">
                {Math.round(glassOpacity * 100)}%
              </span>
            </div>
            <input
              aria-label="毛玻璃透明度"
              type="range"
              min={0.3}
              max={1}
              step={0.05}
              value={glassOpacity}
              onChange={(e) => setGlassOpacity(parseFloat(e.target.value))}
              className="h-2 w-full cursor-pointer appearance-none rounded-full bg-muted accent-primary"
            />
            <div className="mt-1.5 flex justify-between text-[10px] text-muted-foreground">
              <span>透明（看到背景）</span>
              <span>不透明（清晰易读）</span>
            </div>
            <div className="mt-3 text-[11px] text-muted-foreground">
              实时生效，立即看到效果。设置自动保存。
            </div>

            {/* 毛玻璃背景渐变 */}
            <div className="mt-5 mb-2 flex items-center justify-between">
              <label className="text-[12px] text-muted-foreground">背景渐变</label>
              <span className="font-mono text-[11px] text-muted-foreground">
                {BACKGROUND_OPTIONS.find((o) => o.key === background)?.label ?? 'Moonlit Mint'}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {BACKGROUND_OPTIONS.map((opt) => (
                <button
                  key={opt.key}
                  onClick={() => setBackground(opt.key)}
                  className={
                    'relative h-16 rounded-md overflow-hidden transition-all ' +
                    (background === opt.key
                      ? 'ring-2 ring-primary ring-offset-2 ring-offset-background'
                      : 'ring-1 ring-border hover:ring-primary/50')
                  }
                  style={{ background: BACKGROUND_PREVIEW_CSS[opt.key] }}
                  title={`${opt.label} · ${opt.description}`}
                >
                  <span
                    className="absolute bottom-1 left-1.5 text-[10px] font-medium text-white"
                    style={{ textShadow: '0 1px 3px rgba(0,0,0,0.7)' }}
                  >
                    {opt.label}
                  </span>
                </button>
              ))}
            </div>
            <div className="mt-1.5 text-[11px] text-muted-foreground">
              切换主题后背景会自动恢复默认（Moonlit Mint）。
            </div>
          </div>
        ) : (
          <div className="rounded-md bg-muted/20 p-3 text-[12px] text-muted-foreground">
            切换到 macOS 毛玻璃主题后，这里会出现透明度调节。
          </div>
        )}
      </Card>

  )
}
