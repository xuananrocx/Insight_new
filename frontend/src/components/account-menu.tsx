import * as Menu from '@radix-ui/react-dropdown-menu'
import { ChevronRight, ChevronsUpDown, LogOut, ShieldCheck, SunMoon } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/hooks/use-auth'
import { useTheme } from '@/hooks/use-theme'
import { THEMES } from '@/lib/themes'
import { cn } from '@/lib/utils'

export function AccountMenu({ collapsed = false, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const { user, logout } = useAuth()
  const { theme, setTheme, followsDefault, resetTheme } = useTheme()
  const navigate = useNavigate()
  const roles = user.roles.filter(role => role.enabled).map(role => role.name).join('、') || '无启用角色'
  const initial = Array.from(user.username.trim())[0]?.toLocaleUpperCase() || '?'
  const itemClass = 'my-1 flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-[13px] outline-none focus:bg-accent'
  return <Menu.Root>
    <Menu.Trigger asChild><button aria-label={`${user.username} 的账户菜单`} title={collapsed ? user.username : undefined} className={cn('flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-accent/60 data-[state=open]:bg-accent', collapsed && 'justify-center px-0')}>
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border bg-muted text-xs font-medium" aria-hidden="true">{initial}</span>
      {!collapsed && <><span className="min-w-0 flex-1"><span className="block truncate text-[13px]">{user.username}</span></span><ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /></>}
    </button></Menu.Trigger>
    <Menu.Portal><Menu.Content side="right" align="end" sideOffset={28} collisionPadding={12} className="sidebar-popup z-[90] w-60 rounded-xl border bg-popover p-1.5 text-popover-foreground shadow-lg">
      <Menu.Label className="px-3 py-2 font-normal"><div className="break-words text-sm font-medium">{user.username}</div><div className="mt-1 break-words text-xs text-muted-foreground">{roles}</div><div className="mt-1 text-[10px] text-muted-foreground">Insight · {import.meta.env.VITE_APP_VERSION}</div></Menu.Label>
      <Menu.Separator className="mx-2 my-1 h-px bg-border" />
      <Menu.Sub>
        <Menu.SubTrigger className={cn(itemClass, 'data-[state=open]:bg-accent')}><SunMoon className="h-4 w-4" /><span>外观</span><span className="ml-auto text-xs text-muted-foreground">{THEMES.find(t => t.key === theme)?.label}</span><ChevronRight className="h-3.5 w-3.5" /></Menu.SubTrigger>
        <Menu.Portal><Menu.SubContent sideOffset={8} collisionPadding={12} className="sidebar-popup z-[100] w-52 rounded-xl border bg-popover p-1.5 text-popover-foreground shadow-lg">
          <Menu.RadioGroup value={followsDefault ? 'system' : theme} onValueChange={value => { if (value === 'system') resetTheme(); else { const selected = THEMES.find(t => t.key === value); if (selected) setTheme(selected.key) } }}>
            <Menu.RadioItem value="system" className={itemClass}><span className="flex w-4 justify-center"><Menu.ItemIndicator>✓</Menu.ItemIndicator></span>使用系统默认</Menu.RadioItem>
            <Menu.Separator className="mx-2 my-1 h-px bg-border" />
            {THEMES.map(t => <Menu.RadioItem key={t.key} value={t.key} className={itemClass}><span className="flex w-4 justify-center"><Menu.ItemIndicator>✓</Menu.ItemIndicator></span>{t.label}</Menu.RadioItem>)}
          </Menu.RadioGroup>
        </Menu.SubContent></Menu.Portal>
      </Menu.Sub>
      <Menu.Separator className="mx-2 my-1 h-px bg-border" />
      <Menu.Item className={itemClass} onSelect={() => { navigate('/settings?section=account'); onNavigate?.() }}><ShieldCheck className="h-4 w-4" />账号与安全</Menu.Item>
      <Menu.Item className={cn(itemClass, 'text-destructive focus:bg-destructive/10')} onSelect={() => void logout()}><LogOut className="h-4 w-4" />退出登录</Menu.Item>
    </Menu.Content></Menu.Portal>
  </Menu.Root>
}
