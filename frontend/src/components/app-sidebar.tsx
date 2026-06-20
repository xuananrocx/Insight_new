import { NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  MessageSquare,
  Library,
  CheckCircle,
  BarChart3,
  Settings,
  ScrollText,
  Database,
  Bot,
  PanelLeftClose,
  PanelLeftOpen,
  type LucideIcon,
} from 'lucide-react'

import { ChatHistory } from '@/components/chat-history'
import { InsightLogo } from '@/components/insight-logo'
import { useChatSessionsCtx } from '@/hooks/chat-session-context'
import { useLocalStorage } from '@/hooks/use-local-storage'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

type SidebarItem = {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
  /** badge 数字；null/0/undefined 都不显示 */
  count?: number | null
  /** badge 配色：'danger' 用红色（审批待办等），其他用灰色 */
  countTone?: 'danger' | 'default'
}

// 静态项（无 badge 的）；带 badge 的项在组件里基于 stats 拼接
const STATIC_ITEMS: SidebarItem[] = [
  { to: '/', label: '提问', icon: MessageSquare, end: true },
  { to: '/kbs', label: '知识库管理', icon: Database },
  { to: '/stats', label: '分析', icon: BarChart3 },
  { to: '/ai-logs', label: 'AI 日志', icon: Bot },
  { to: '/settings', label: '设置', icon: Settings },
]

// 默认菜单顺序（存到 localStorage 后可被用户覆盖）
const DEFAULT_MENU_ORDER = [
  '/',
  '/knowledge',
  '/kbs',
  '/review',
  '/stats',
  '/ai-logs',
  '/settings',
]

export function AppSidebar() {
  const ctx = useChatSessionsCtx()
  const [showLogs] = useLocalStorage<boolean>('amd-ui-show-logs', false)
  const [collapsed, setCollapsed] = useLocalStorage<boolean>('amd-ui-sidebar-collapsed', false)
  const [menuOrder] = useLocalStorage<string[]>('amd-ui-menu-order', DEFAULT_MENU_ORDER)

  // 拉 stats 提供 badge 数字。staleTime 60s 避免短时间内重复请求。
  const stats = useQuery({
    queryKey: ['knowledge-stats', 'sidebar'],
    queryFn: () => api.knowledge.stats(),
    staleTime: 60_000,
  })

  const items: SidebarItem[] = [
    STATIC_ITEMS[0],  // 提问
    {
      to: '/knowledge',
      label: '文档管理',
      icon: Library,
      // 文档总数；0 时不显示
      count: stats.data?.files_total || null,
    },
    STATIC_ITEMS[1],  // 知识库管理
    {
      to: '/review',
      label: '审批',
      icon: CheckCircle,
      // 待审批数；0 时不显示
      count: stats.data?.feedback_pending || null,
      countTone: 'danger',
    },
    ...STATIC_ITEMS.slice(2),  // 分析、设置
  ]
  const allItems = showLogs
    ? [...items, { to: '/logs', label: '系统日志', icon: ScrollText }]
    : items

  // 按 menuOrder 重排（缺失的项追加到末尾，多余的保留）
  const orderedItems: SidebarItem[] = (() => {
    const byPath = new Map(allItems.map((it) => [it.to, it]))
    const ordered: SidebarItem[] = []
    const seen = new Set<string>()
    for (const p of menuOrder) {
      const it = byPath.get(p)
      if (it && !seen.has(p)) {
        ordered.push(it)
        seen.add(p)
      }
    }
    for (const it of allItems) {
      if (!seen.has(it.to)) ordered.push(it)
    }
    return ordered
  })()

  return (
    <aside
      className={cn(
        'flex h-full flex-col border-r border-white/10 bg-sidebar transition-[width] duration-150 md:flex',
        collapsed ? 'w-[56px]' : 'w-[220px]',
      )}
    >
      {/* Logo 行：Logo + 标题 + 折叠按钮 */}
      <div className={cn('flex items-center py-4', collapsed ? 'justify-center px-2' : 'gap-2.5 px-4')}>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#1F2937]">
          <InsightLogo size="sm" variant="solid" />
        </div>
        {!collapsed ? (
          <>
            <span className="flex-1 text-[13px] font-semibold leading-tight">Insight</span>
            <button
              onClick={() => setCollapsed(true)}
              className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
              title="收起侧边栏"
            >
              <PanelLeftClose className="h-4 w-4" />
            </button>
          </>
        ) : null}
      </div>

      {/* 折叠态：单独的展开按钮放在 Logo 下方 */}
      {collapsed ? (
        <div className="px-2 pb-1">
          <button
            onClick={() => setCollapsed(false)}
            className="flex w-full items-center justify-center rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            title="展开侧边栏"
          >
            <PanelLeftOpen className="h-4 w-4" />
          </button>
        </div>
      ) : null}

      <div className={cn('px-2 py-2', collapsed ? 'px-2' : 'px-3')}>
        {!collapsed ? (
          <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            主菜单
          </div>
        ) : null}
        <nav className={cn('space-y-0.5', collapsed ? 'mt-0' : 'mt-1')}>
          {orderedItems.map((item) => {
            const Icon = item.icon
            const isHome = item.to === '/'
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={() => ctx.clearActive()}
                title={collapsed ? item.label : undefined}
                className={({ isActive }) => {
                    const effectiveActive = isHome ? isActive && !ctx.activeId : isActive
                    return cn(
                      'flex items-center rounded-md text-[13px] transition-colors',
                      collapsed
                        ? 'h-9 w-full justify-center'
                        : 'gap-2.5 px-2.5 py-1.5',
                      effectiveActive
                        ? 'bg-accent font-medium text-accent-foreground'
                        : 'text-foreground/75 hover:bg-accent/60 hover:text-foreground',
                    )
                  }}
              >
                <Icon className="h-3.5 w-3.5 shrink-0" />
                {!collapsed ? <span className="flex-1">{item.label}</span> : null}
                {!collapsed && item.count != null && item.count > 0 ? (
                  <span
                    className={cn(
                      'rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                      item.countTone === 'danger'
                        ? 'bg-destructive text-white'
                        : 'bg-muted text-muted-foreground',
                    )}
                  >
                    {item.count}
                  </span>
                ) : null}
              </NavLink>
            )
          })}
        </nav>
      </div>

      {!collapsed ? (
        <div className="mt-1 flex-1 overflow-hidden border-t border-white/5">
          <ChatHistory />
        </div>
      ) : null}

      <div className={cn('border-t border-white/5', collapsed ? 'px-2 py-2 text-center' : 'px-4 py-2.5')}>
        <span className="text-[10px] font-medium text-muted-foreground">V0.5</span>
      </div>
    </aside>
  )
}
