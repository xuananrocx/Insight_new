import { PersonalAccentPicker } from '@/components/accent-picker'
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

  const visibleOrder = visibleMenuOrder(menuOrder, can, showLogs).filter(path => ['/review', '/stats', '/ai-logs', '/logs', '/admin/knowledge-access'].includes(path))
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
        调整工作台二级菜单的显示顺序。顶部导航与底部账号入口保持固定；侧栏宽度可拖动分界线调整，双击恢复默认。偏好在当前浏览器中按账号保存。
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
        {theme === 'minimal' && <PersonalAccentPicker />}
        {theme === 'macos' ? (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <label className="text-[12px] text-muted-foreground">玻璃浓度</label>
              <span className="font-mono text-[11px] text-muted-foreground">
                {Math.round(glassOpacity * 100)}%
              </span>
            </div>
            <input
              aria-label="玻璃浓度"
              type="range"
              min={0.3}
              max={1}
              step={0.05}
              value={glassOpacity}
              onChange={(e) => setGlassOpacity(parseFloat(e.target.value))}
              className="h-2 w-full cursor-pointer appearance-none rounded-full bg-muted accent-primary"
            />
            <div className="mt-1.5 flex justify-between text-[10px] text-muted-foreground">
              <span>通透（背景更明显）</span>
              <span>厚实（内容更清晰）</span>
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
            <div role="group" aria-label="毛玻璃背景" className="grid max-w-md grid-cols-3 gap-2 sm:grid-cols-5">
              {BACKGROUND_OPTIONS.map((opt) => (
                <button
                  key={opt.key}
                  onClick={() => setBackground(opt.key)}
                  type="button"
                  aria-label={opt.label}
                  aria-pressed={background === opt.key}
                  className={`flex flex-col items-center gap-1.5 rounded-md border px-2 py-2 text-[11px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${background === opt.key ? 'border-input bg-muted' : 'border-transparent hover:bg-muted/60'}`}
                  title={`${opt.label} · ${opt.description}`}
                >
                  <span className="flex h-7 w-7 items-center justify-center rounded-full border border-white/20 text-white" style={{ background: BACKGROUND_PREVIEW_CSS[opt.key] }} aria-hidden="true">{background === opt.key ? '✓' : ''}</span>
                  <span>{opt.label}</span>
                </button>
              ))}
            </div>
            <div className="mt-1.5 text-[11px] text-muted-foreground">
              背景与玻璃浓度按当前账号保存在此浏览器，切换主题或刷新后保留。
            </div>
          </div>
        ) : (
          <div className="rounded-md bg-muted/20 p-3 text-[12px] text-muted-foreground">
            切换到 磨砂玻璃主题后，这里会出现玻璃浓度调节。
          </div>
        )}
      </Card>

  )
}
