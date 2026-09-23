import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { accountKey, accountRequest } from '@/lib/account-api'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Toggle } from '@/components/ui/toggle-switch'
import { ManagementDialog } from '@/components/management-dialog'

type Preferences = { content: string; enabled: boolean; version: string }

export function PersonalPreferencesCard() {
  const qc = useQueryClient()
  const key = [accountKey('personal-preferences')]
  const query = useQuery({ queryKey: key, queryFn: () => accountRequest<Preferences>('/account/preferences') })
  const [draft, setDraft] = useState<Preferences | null>(null)
  const save = useMutation({
    mutationFn: (value: Preferences) => accountRequest<Preferences>('/account/preferences', 'PUT', value),
    onSuccess: value => { qc.setQueryData(key, value); setDraft(null); toast.success('个人偏好已保存，下次提问生效') },
    onError: (error: Error) => { toast.error(error.message); void query.refetch() },
  })
  return <Card className="space-y-4 p-5">
    <div className="flex items-center justify-between gap-4"><h2 className="font-semibold">个人偏好</h2>
      <Toggle label="使用个人偏好" checked={query.data?.enabled ?? true} disabled={!query.data || save.isPending} onChange={enabled => { if (query.data) save.mutate({ ...query.data, enabled }) }} />
    </div>
    <p className="text-sm text-muted-foreground">记录回答风格、常用环境和协作习惯。增强AI和深度AI 都会参考；当前问题的明确要求优先。仅由你编辑，AI 不会自动修改。</p>
    {query.error && <p className="text-sm text-destructive">{query.error.message}<Button variant="ghost" onClick={() => void query.refetch()}>重新加载</Button></p>}
    <div className="flex items-center gap-3"><Button variant="outline" disabled={!query.data || save.isPending} onClick={() => setDraft({ ...query.data! })}>编辑个人偏好</Button><span className="text-xs text-muted-foreground">{query.data?.content.length ?? 0} / 4000 字符</span></div>
    {draft && <ManagementDialog title="编辑个人偏好" busy={save.isPending} onClose={() => setDraft(null)}>
      <p className="text-sm text-muted-foreground">支持 Markdown。只对你的账号生效；清空并保存即可移除内容。</p>
      <textarea aria-label="个人偏好内容" value={draft.content} maxLength={4000} onChange={e => setDraft({ ...draft, content: e.target.value })} className="min-h-64 w-full resize-y rounded-md border bg-background p-3 text-sm leading-relaxed" placeholder={'## 回答偏好\n- 默认中文，先给结论。\n\n## 常用环境\n- 日常开发使用 Windows，实际部署以当前会话为准。'} />
      <div className="flex items-center justify-between"><span className="text-xs text-muted-foreground">{draft.content.length} / 4000</span><div className="flex gap-2"><Button variant="outline" disabled={save.isPending} onClick={() => setDraft(null)}>取消</Button><Button disabled={save.isPending} onClick={() => save.mutate(draft)}>{save.isPending ? '保存中…' : '保存'}</Button></div></div>
    </ManagementDialog>}
  </Card>
}
