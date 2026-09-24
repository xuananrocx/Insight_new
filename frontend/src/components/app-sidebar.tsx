import { adminLinks, DEFAULT_MENU_ORDER, visibleMenuOrder } from '@/lib/navigation'
import { useAuth } from '@/hooks/use-auth'
import { useId, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
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
  ChevronDown,
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

function SidebarGroup({ label, open, collapsed, activeLabel, onToggle, children }: {
  label: string
  open: boolean
  collapsed: boolean
  activeLabel?: string
  onToggle: () => void
  children: ReactNode
}) {
  const id = useId()
  return <section className="space-y-1">
    {!collapsed && <button type="button" aria-expanded={open} aria-controls={id} onClick={onToggle}
      title={!open && activeLabel ? `当前页面：${activeLabel}` : undefined}
      className={cn('flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[11px] font-semibold transition-colors hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        !open && activeLabel ? 'bg-primary/10 text-primary' : 'text-muted-foreground')}>
      <ChevronDown aria-hidden="true" className={cn('h-3.5 w-3.5 shrink-0 transition-transform', !open && '-rotate-90')} />
      <span className="flex-1">{label}</span>
      {!open && activeLabel && <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-primary" />}
    </button>}
    <nav id={id} aria-label={label} hidden={!collapsed && !open} className="space-y-0.5">{children}</nav>
  </section>
}

// 静态项（无 badge 的）；带 badge 的项在组件里基于 stats 拼接
const STATIC_ITEMS: SidebarItem[] = [
  { to: '/', label: '提问', icon: MessageSquare, end: true },
  { to: '/kbs', label: '知识库管理', icon: Database },
  { to: '/stats', label: '分析', icon: BarChart3 },
  { to: '/ai-logs', label: 'AI 日志', icon: Bot },
  { to: '/settings', label: '个人设置', icon: Settings },
]

export function AppSidebar() {
  const { can } = useAuth()
  const { pathname } = useLocation()
  const ctx = useChatSessionsCtx()
  const [showLogs] = useLocalStorage<boolean>('amd-ui-show-logs', false)
  const [collapsed, setCollapsed] = useLocalStorage<boolean>('amd-ui-sidebar-collapsed', false)
  const [mainOpen, setMainOpen] = useLocalStorage<boolean>('amd-ui-main-menu-open', true)
  const [adminOpen, setAdminOpen] = useLocalStorage<boolean>('amd-ui-admin-menu-open', false)
  const [menuOrder] = useLocalStorage<string[]>('amd-ui-menu-order', DEFAULT_MENU_ORDER)

  // 拉 stats 提供 badge 数字。staleTime 60s 避免短时间内重复请求。
  const stats = useQuery({
    queryKey: ['knowledge-stats', 'sidebar'],
    queryFn: () => api.knowledge.stats(),
    staleTime: 60_000,
    enabled: can('documents.view') || can('feedback.view') || can('analysis.view'),
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
  const allItems: SidebarItem[] = [...items, { to: '/logs', label: '系统日志', icon: ScrollText }]
  const byPath = new Map(allItems.map(item => [item.to, item]))
  const orderedItems = visibleMenuOrder(menuOrder, can, showLogs)
    .map(path => byPath.get(path))
    .filter((item): item is SidebarItem => !!item)
  const visibleAdminItems = adminLinks.filter(item => !item.permission || can(item.permission))
  const matchesPage = (to: string) => to === '/' ? pathname === '/' && !ctx.activeId : pathname === to || pathname.startsWith(`${to}/`)
  const activeMain = orderedItems.find(item => matchesPage(item.to))?.label
  const activeAdmin = visibleAdminItems.find(item => matchesPage(item.to) || (item.to === '/admin/users' && pathname === '/accounts'))?.label

  return (
    <aside
      className={cn(
        'hidden h-full min-h-0 shrink-0 flex-col overflow-hidden border-r border-white/10 bg-sidebar transition-[width] duration-150 md:flex',
        collapsed ? 'w-[56px]' : 'w-[220px]',
      )}
    >
      {/* Logo 行：Logo + 标题 + 折叠按钮 */}
      <div className={cn('flex shrink-0 items-center py-4', collapsed ? 'justify-center px-2' : 'gap-2.5 px-4')}>
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
        <div className="shrink-0 px-2 pb-1">
          <button
            onClick={() => setCollapsed(false)}
            className="flex w-full items-center justify-center rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            title="展开侧边栏"
          >
            <PanelLeftOpen className="h-4 w-4" />
          </button>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden" data-sidebar-body>
      <div data-sidebar-menus className={cn('min-h-0 space-y-3 overflow-y-auto overscroll-contain py-2', collapsed ? 'flex-1 px-2' : 'max-h-[66.666%] shrink-0 px-3')}>
        <SidebarGroup label="主菜单" open={mainOpen} collapsed={collapsed} activeLabel={activeMain} onToggle={() => setMainOpen(value => !value)}>
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
        </SidebarGroup>
        {visibleAdminItems.length > 0 && <SidebarGroup label="系统管理" open={adminOpen} collapsed={collapsed} activeLabel={activeAdmin} onToggle={() => setAdminOpen(value => !value)}>
          {visibleAdminItems.map(item => <NavLink key={item.to} to={item.to} title={item.label} className={({ isActive }) => cn('flex items-center rounded-md text-[13px]', collapsed ? 'h-9 w-full justify-center' : 'gap-2 px-2.5 py-1.5', isActive ? 'bg-accent font-medium' : 'text-foreground/75 hover:bg-accent/60')}><Settings className="h-3.5 w-3.5 shrink-0" />{!collapsed && <span>{item.label}</span>}</NavLink>)}
        </SidebarGroup>}
      </div>

      {!collapsed ? (
        <div data-sidebar-history className="min-h-0 flex-1 overflow-hidden border-t border-white/5">
          <ChatHistory />
        </div>
      ) : null}
      </div>

      <div className={cn('shrink-0 border-t border-white/5', collapsed ? 'px-2 py-2 text-center' : 'px-4 py-2.5')}>
        <span className="text-[10px] font-medium text-muted-foreground">V0.5</span>
      </div>
    </aside>
  )
}
