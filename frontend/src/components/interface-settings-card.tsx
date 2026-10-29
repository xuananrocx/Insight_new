import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { useAuth } from '@/hooks/use-auth'
import { accountRequest } from '@/lib/account-api'
import { THEMES } from '@/lib/themes'
import type { ThemeKey } from '@/hooks/use-theme'
import { Card } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Toggle } from '@/components/ui/toggle-switch'
import { useLocalStorage } from '@/hooks/use-local-storage'

export function InterfaceSettingsCard() {
  const [autoExpand, setAutoExpand] = useLocalStorage('amd-chat-header-auto-expand', true)
  return <Card className="space-y-4 p-5">
    <div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium">会话顶部</h3><span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">本地偏好</span></div>
    <div className="flex items-start justify-between gap-4">
      <div><p className="text-sm">鼠标悬停自动展开</p><p className="mt-1 text-xs leading-relaxed text-muted-foreground">默认开启。鼠标停留在会话顶部区域时，自动展开会话信息与知识库选择；关闭后可点击箭头展开。</p></div>
      <Toggle label="鼠标悬停自动展开会话顶部" checked={autoExpand} onChange={setAutoExpand} />
    </div>
    <p className="border-t pt-3 text-xs leading-relaxed text-muted-foreground">自动保存，仅影响当前账号在此浏览器中的所有会话，不影响其他用户。顶部图钉仍可固定当前展开状态。</p>
  </Card>
}

export function DefaultThemeSettingsCard() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['interface-settings'], queryFn: () => accountRequest<{ default_theme: ThemeKey }>('/settings/interface') })
  const mutation = useMutation({
    mutationFn: (default_theme: ThemeKey) => accountRequest<{ default_theme: ThemeKey }>('/settings/interface', 'PUT', { default_theme }),
    onSuccess: data => {
      qc.setQueryData(['interface-settings'], data)
      void qc.invalidateQueries({ queryKey: ['appearance-default'] })
      toast.success('系统默认主题已保存')
    },
    onError: (error: Error) => toast.error(error.message),
  })
  return <Card className="space-y-3 p-5">
    <div className="flex items-center gap-2"><h3 className="text-sm font-medium">默认主题</h3><span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">全局设置</span></div>
    <p className="text-xs leading-relaxed text-muted-foreground">首次使用或未选择个人主题的用户使用此主题。个人设置中的主题优先，不会被这里的修改覆盖；选择“使用系统默认”可恢复跟随。</p>
    {query.isError ? <p role="alert" className="text-sm text-destructive">{query.error.message}<button className="ml-2 underline" onClick={() => void query.refetch()}>重试</button></p> : <Select value={query.data?.default_theme ?? 'minimal'} disabled={!query.data || mutation.isPending || !can('system.edit')} onValueChange={value => mutation.mutate(value as ThemeKey)}>
      <SelectTrigger aria-label="系统默认主题" className="h-9 w-full max-w-xs"><SelectValue /></SelectTrigger>
      <SelectContent>{THEMES.map(t => <SelectItem key={t.key} value={t.key}>{t.label}</SelectItem>)}</SelectContent>
    </Select>}
    <p className="text-xs text-muted-foreground">{mutation.isPending ? '正在保存…' : '自动保存，未设置时默认为简约灰白。个人偏好按账号保存在当前浏览器。'}</p>
  </Card>
}
