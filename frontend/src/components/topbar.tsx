import { useAuth } from '@/hooks/use-auth'
import { ChevronDown, ChevronRight, LogOut, Plus, Settings2, ShieldCheck } from 'lucide-react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { ThemeToggle } from '@/components/theme-toggle'
import { useChatSessionsCtx } from '@/hooks/chat-session-context'

export function Topbar() {
  const { user, logout } = useAuth()
  const ctx = useChatSessionsCtx()
  const navigate = useNavigate()
  const initial = Array.from(user.username.trim())[0]?.toLocaleUpperCase() || '?'
  const roles = user.roles.filter(role => role.enabled).map(role => role.name).join('、') || '无启用角色'
  const menuItem = 'flex cursor-pointer items-center gap-2 rounded-md px-3 py-2.5 text-sm outline-none focus:bg-accent focus:text-accent-foreground'

  return (
    <header className="flex h-12 items-center justify-between border-b bg-background px-4">
      <div className="hidden items-center gap-2 sm:flex">
        <span className="text-[12px] text-muted-foreground">Workspace</span>
        <span className="text-[12px] text-muted-foreground">/</span>
        <span className="text-[13px] font-medium">Insight</span>
        <span className="ml-2 inline-flex h-1.5 w-1.5 rounded-full bg-success" title="服务正常" />
      </div>

      <div className="ml-auto flex items-center gap-2">
        <Button
          size="sm"
          className="gap-1.5 text-[12px]"
          onClick={() => {
            ctx.clearActive()
            navigate('/')
          }}
          title="开始新对话"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>新提问</span>
        </Button>
        <ThemeToggle compact />
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <Button size="sm" variant="ghost" aria-label={`${user.username} 的账户菜单`} className="h-auto gap-2 rounded-lg py-1 pl-1 pr-2 data-[state=open]:bg-accent">
              <span aria-hidden="true" className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-medium text-primary">{initial}</span>
              <span className="hidden max-w-32 truncate text-xs sm:block">{user.username}</span>
              <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            </Button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content align="end" sideOffset={8} collisionPadding={8} className="z-[90] w-60 max-w-[calc(100vw-1rem)] rounded-xl border bg-popover p-1.5 text-popover-foreground shadow-lg">
              <DropdownMenu.Label className="flex items-center gap-3 px-3 py-3 font-normal">
                <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/15 text-sm font-medium text-primary">{initial}</span>
                <div className="min-w-0"><p className="break-words text-sm font-medium">{user.username}</p><p className="mt-0.5 break-words text-xs text-muted-foreground">{roles}</p></div>
              </DropdownMenu.Label>
              <DropdownMenu.Separator className="mx-1 my-1 h-px bg-border" />
              <DropdownMenu.Item className={menuItem} onSelect={() => navigate('/settings?section=appearance')}><Settings2 aria-hidden="true" className="h-4 w-4 text-muted-foreground" />个人设置<ChevronRight aria-hidden="true" className="ml-auto h-3.5 w-3.5 text-muted-foreground" /></DropdownMenu.Item>
              <DropdownMenu.Item className={menuItem} onSelect={() => navigate('/settings?section=account')}><ShieldCheck aria-hidden="true" className="h-4 w-4 text-muted-foreground" />账号与安全<ChevronRight aria-hidden="true" className="ml-auto h-3.5 w-3.5 text-muted-foreground" /></DropdownMenu.Item>
              <DropdownMenu.Separator className="mx-1 my-1 h-px bg-border" />
              <DropdownMenu.Item className={`${menuItem} focus:bg-destructive/10 focus:text-destructive`} onSelect={() => void logout()}><LogOut aria-hidden="true" className="h-4 w-4" />退出登录</DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>
    </header>
  )
}
