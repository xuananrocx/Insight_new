import { useEffect, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import * as Menu from '@radix-ui/react-dropdown-menu'
import * as Dialog from '@radix-ui/react-dialog'
import { SquarePen, Library, Files, CheckCircle, BarChart3, Settings, ScrollText, Users, ShieldCheck, LockKeyhole, Network, Bot, PanelLeftClose, PanelLeftOpen, ChevronRight, PanelsTopLeft, X, type LucideIcon } from 'lucide-react'
import { adminLinks, DEFAULT_MENU_ORDER, visibleMenuOrder } from '@/lib/navigation'
import { useAuth } from '@/hooks/use-auth'
import { ChatHistory } from '@/components/chat-history'
import { InsightLogo } from '@/components/insight-logo'
import { AccountMenu } from '@/components/account-menu'
import { useChatSessionsCtx } from '@/hooks/chat-session-context'
import { useLocalStorage } from '@/hooks/use-local-storage'
import { useSidebarWidth } from '@/hooks/use-sidebar-width'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

type Item = { to: string; label: string; icon: LucideIcon; count?: number; section?: string }

const adminIcons: Record<string, LucideIcon> = {
  '/admin/users': Users,
  '/admin/roles': ShieldCheck,
  '/admin/team-api': Network,
  '/admin/audit': ScrollText,
  '/system-settings': Settings,
}

function SidebarContent({ collapsed, mobile = false, onToggle, onNavigate }: { collapsed: boolean; mobile?: boolean; onToggle: () => void; onNavigate?: () => void }) {
  const { can } = useAuth()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const ctx = useChatSessionsCtx()
  const [showLogs] = useLocalStorage<boolean>('amd-ui-show-logs', false)
  const [menuOrder] = useLocalStorage<string[]>('amd-ui-menu-order', DEFAULT_MENU_ORDER)
  const [openMenu, setOpenMenu] = useState<string | null>(null)
  const stats = useQuery({ queryKey: ['knowledge-stats', 'sidebar'], queryFn: () => api.knowledge.stats(), staleTime: 60_000, enabled: can('documents.view') || can('feedback.view') || can('analysis.view') })
  const available = visibleMenuOrder(menuOrder, can, showLogs)
  const workItems: Item[] = [
    { to: '/review', label: '审批', icon: CheckCircle, count: stats.data?.feedback_pending },
    { to: '/stats', label: '分析', icon: BarChart3 },
    { to: '/ai-logs', label: 'AI 日志', icon: Bot },
    { to: '/logs', label: '系统日志', icon: ScrollText },
    { to: '/admin/knowledge-access', label: '知识库授权', icon: LockKeyhole },
  ].filter(item => available.includes(item.to)).sort((a, b) => available.indexOf(a.to) - available.indexOf(b.to))
  const settingsItems: Item[] = [
    { to: '/settings', label: '个人设置', icon: Settings, section: '个人' },
    ...adminLinks.filter(item => !item.permission || can(item.permission)).map(item => ({ ...item, icon: adminIcons[item.to] ?? Settings, section: '管理' })),
  ]
  const go = (to: string) => { navigate(to); onNavigate?.() }
  const matches = (to: string) => pathname === to || pathname.startsWith(`${to}/`) || (to === '/admin/users' && pathname === '/accounts')
  const menu = (label: string, Icon: LucideIcon, items: Item[]) => items.length > 0 && <Menu.Root open={openMenu === label} onOpenChange={open => setOpenMenu(open ? label : null)}>
    <Menu.Trigger asChild><button type="button" aria-label={label} title={collapsed ? label : undefined} className={cn('sidebar-menu-trigger', collapsed && 'is-collapsed', items.some(item => matches(item.to)) && 'bg-accent text-accent-foreground')}>
      <Icon className="h-4 w-4" />{!collapsed && <><span>{label}</span><ChevronRight className="h-3.5 w-3.5 text-muted-foreground" /></>}
    </button></Menu.Trigger>
    <Menu.Portal><Menu.Content side="right" align="end" sideOffset={28} collisionPadding={12} className="sidebar-popup space-y-1 z-[90] w-60 rounded-xl border bg-popover p-1.5 text-popover-foreground shadow-lg">
      {items.map((item, index) => <div key={item.to}>
        {item.section && item.section !== items[index - 1]?.section && <>{index > 0 && <Menu.Separator className="mx-2 my-1 h-px bg-border" />}<Menu.Label className="px-3 py-2 text-[11px] font-normal text-muted-foreground">{item.section}</Menu.Label></>}
        <Menu.Item onSelect={() => go(item.to)} className={cn('flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-[13px] outline-none focus:bg-accent', matches(item.to) && 'bg-accent/60')}><item.icon className="h-4 w-4 shrink-0 text-muted-foreground" /><span className="flex-1">{item.label}</span>{!!item.count && <span className="rounded-full bg-destructive/10 px-1.5 text-xs text-destructive">{item.count}</span>}</Menu.Item>
      </div>)}
    </Menu.Content></Menu.Portal>
  </Menu.Root>
  return <>
    <div className={cn('flex h-16 shrink-0 items-center gap-2 px-4', collapsed && 'justify-center px-2')}>
      {!collapsed && <><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#1F2937]"><InsightLogo size="sm" variant="solid" /></span><span className="flex-1 text-sm font-semibold">Insight</span></>}
      <button type="button" onClick={onToggle} aria-label={mobile ? '关闭导航' : collapsed ? '展开侧栏' : '收起侧栏'} title={mobile ? '关闭导航' : collapsed ? '展开侧栏' : '收起侧栏'} className="rounded-md p-2 text-muted-foreground hover:bg-accent">{mobile ? <X className="h-4 w-4" /> : collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}</button>
    </div>
    <nav aria-label="主导航" className={cn('shrink-0 space-y-1 px-3 pb-3', collapsed && 'px-2')}>
      <button type="button" aria-label="新对话" title={collapsed ? '新对话' : undefined} className={cn('flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-[13px] text-foreground/80 hover:bg-accent/60', collapsed && 'justify-center px-0')} onClick={() => { ctx.setNewGroupId(null); ctx.clearActive(); go('/') }}><SquarePen className="h-4 w-4 shrink-0" />{!collapsed && '新对话'}</button>
      {([{ to: '/kbs', label: '知识库', icon: Library }, { to: '/knowledge', label: '文档', icon: Files }] satisfies Item[]).filter(item => available.includes(item.to)).map(item => <NavLink key={item.to} to={item.to} onClick={onNavigate} title={collapsed ? item.label : undefined} aria-label={item.label} className={({ isActive }) => cn('flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-[13px] hover:bg-accent/60', collapsed && 'justify-center px-0', isActive ? 'bg-accent font-medium' : 'text-foreground/80')}><item.icon className="h-4 w-4 shrink-0" />{!collapsed && item.label}</NavLink>)}
    </nav>
    {!collapsed && <div data-sidebar-history className="min-h-0 flex-1 overflow-hidden px-2"><ChatHistory onNavigate={onNavigate} /></div>}
    <div className={cn('mt-auto shrink-0 space-y-1 border-t border-border/60 px-3 py-2', collapsed && 'px-2')}>
      {menu('工作台', PanelsTopLeft, workItems)}
      {menu('设置', Settings, settingsItems)}
      <div className="pt-1"><AccountMenu collapsed={collapsed} onNavigate={onNavigate} /></div>
    </div>
  </>
}

export function AppSidebar() {
  const [collapsed, setCollapsed] = useLocalStorage<boolean>('amd-ui-sidebar-collapsed', false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const { width, dragging, separatorProps } = useSidebarWidth()
  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 768px)')
    const closeDrawer = () => { if (desktop.matches) setMobileOpen(false) }
    desktop.addEventListener('change', closeDrawer)
    return () => desktop.removeEventListener('change', closeDrawer)
  }, [])
  return <>
    <aside aria-label="导航与会话" className="app-sidebar relative hidden h-full min-h-0 shrink-0 flex-col border-r border-border/60 bg-sidebar md:flex" style={{ width: collapsed ? 62 : width }}>
      <SidebarContent collapsed={collapsed} onToggle={() => setCollapsed(value => !value)} />
      {!collapsed && <div {...separatorProps} title="拖动调整宽度，双击恢复默认" className={cn('sidebar-resizer', dragging && 'is-dragging')} />}
    </aside>
    <Dialog.Root open={mobileOpen} onOpenChange={setMobileOpen}>
      <div className="flex h-11 shrink-0 items-center gap-2 border-b px-2 md:hidden"><Dialog.Trigger asChild><button aria-label="打开导航" className="rounded-md p-2 hover:bg-accent"><PanelLeftOpen className="h-4 w-4" /></button></Dialog.Trigger><span className="text-sm font-medium">Insight</span></div>
      <Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 md:hidden" /><Dialog.Content aria-describedby={undefined} className="app-sidebar fixed inset-y-0 left-0 z-50 flex w-[min(300px,88vw)] flex-col bg-sidebar shadow-xl md:hidden"><Dialog.Title className="sr-only">导航与会话</Dialog.Title><SidebarContent collapsed={false} mobile onToggle={() => setMobileOpen(false)} onNavigate={() => setMobileOpen(false)} /></Dialog.Content></Dialog.Portal>
    </Dialog.Root>
  </>
}
