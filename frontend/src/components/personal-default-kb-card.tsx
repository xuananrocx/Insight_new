import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Library, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { kbLabel } from '@/lib/kb-label'
import { Card } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const SOURCE_LABEL: Record<string, string> = {
  personal: '个人默认',
  system: '系统默认',
  builtin: '内置知识库',
  first: '第一个可见知识库',
}

export function PersonalDefaultKbCard() {
  const qc = useQueryClient()
  const defaultKb = useQuery({
    queryKey: ['settings', 'default_kb'],
    queryFn: () => api.defaultKb.get(),
    staleTime: 60_000,
  })
  const kbList = useQuery({
    queryKey: ['kbs'],
    queryFn: () => api.kb.list(),
    staleTime: 60_000,
  })
  const [pendingId, setPendingId] = useState<string>('')

  const save = useMutation({
    mutationFn: (kbId: string | null) => api.defaultKb.setPersonal(kbId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['settings', 'default_kb'] })
      toast.success(
        data.personal_kb_id
          ? '个人默认知识库已保存'
          : '已清除个人默认，将使用系统默认',
      )
      setPendingId('')
    },
    onError: (e: Error) => {
      toast.error(`保存失败：${e.message}`)
      setPendingId('')
    },
  })

  const currentValue = defaultKb.data?.personal_kb_id ?? ''
  const selected = pendingId || currentValue
  const effective = defaultKb.data

  return (
    <Card className="space-y-3 p-5">
      <div className="flex items-center gap-2">
        <Library className="h-4 w-4 text-muted-foreground" />
        <h2 className="font-semibold">我的默认知识库</h2>
        {currentValue ? (
          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">已自定义</span>
        ) : (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">跟随系统</span>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        提问页和文档管理页会默认选中此知识库，仅对自己的账号生效。留空时使用系统默认知识库。
      </p>
      <div className="flex items-center gap-2">
        <Select
          value={selected || 'inherit'}
          onValueChange={(v) => {
            const id = v === 'inherit' ? '' : v
            setPendingId(id)
            save.mutate(id || null)
          }}
          disabled={save.isPending || kbList.isLoading}
        >
          <SelectTrigger className="flex-1">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="inherit">跟随系统默认</SelectItem>
            {(kbList.data ?? []).map((kb) => (
              <SelectItem key={kb.id} value={kb.id}>
                {kbLabel(kb)}
                {kb.is_default ? '（内置）' : ''}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {save.isPending ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
      </div>
      {effective ? (
        <p className="text-xs text-muted-foreground">
          当前生效：{effective.name ?? '（无可用知识库）'}（来源：{SOURCE_LABEL[effective.source] ?? effective.source}）
        </p>
      ) : null}
      {effective?.fallback_reason ? (
        <p className="text-xs text-warning">
          已配置的默认知识库当前不可用（被删除或失去权限），已自动回退，建议重新选择。
        </p>
      ) : null}
    </Card>
  )
}
