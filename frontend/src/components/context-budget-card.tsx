import { SettingsHelp } from '@/components/settings-help'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api, type ContextBudgetSettings } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

type Key = keyof ContextBudgetSettings
const fields: { key: Key; label: string; min: number; max: number; help: string }[] = [
  { key: 'context_window_tokens', label: '模型容量未知时的默认窗口', min: 16384, max: 262144, help: '已填写模型上下文容量时优先使用模型值；应用目前最多使用 262,144 tokens。' },
  { key: 'answer_reserve_tokens', label: '作答预留预算', min: 1024, max: 131072, help: '查阅时尽量保留的作答空间，不是输出封顶。小窗口会自动缩小预留，必要时压缩资料。' },
  { key: 'history_tokens', label: '近期历史预算', min: 1000, max: 24000, help: '用于近期问答和会话记忆，仍受实际窗口限制。' },
  { key: 'summary_input_tokens', label: '会话记忆摘要输入预算', min: 2000, max: 20000, help: '整理历史对话时使用，与文档摘要配置独立。' },
  { key: 'summary_tokens', label: '会话记忆摘要输出上限', min: 500, max: 4000, help: '还受所选模型最大输出和剩余窗口限制。' },
]

export function ContextBudgetCard() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['settings', 'context_budget'], queryFn: api.contextBudget.get })
  const [draft, setDraft] = useState<ContextBudgetSettings | null>(null)
  const data = query.data
  const form = draft ?? data
  const mutation = useMutation({
    mutationFn: api.contextBudget.update,
    onSuccess: result => {
      qc.setQueryData(['settings', 'context_budget'], result)
      setDraft(null)
      toast.success('上下文预算已保存，新提问生效')
    },
    onError: (error: Error) => toast.error(error.message),
  })
  if (query.isError) return <Card className="mb-4 p-5 text-sm"><p role="alert">上下文配置加载失败：{query.error.message}</p><Button variant="outline" onClick={() => void query.refetch()}>重试</Button></Card>
  if (!form || !data) return <Card className="mb-4 p-5 text-sm text-muted-foreground">正在加载上下文配置…</Card>
  const input = (field: typeof fields[number]) => {
    const locked = data.sources[field.key]?.includes('覆盖')
    return <label key={field.key} className="block text-[13px]">
      <span className="mb-1.5 block font-medium"><SettingsHelp title={<>{field.label}（tokens）</>} label={`${field.label}说明`}>{field.help}{`\n来源：${data.sources[field.key]}${locked ? '；请调整覆盖文件' : ''}`}</SettingsHelp></span>
      <input type="number" required min={field.min} max={field.max} step={1} disabled={locked || mutation.isPending}
        value={form[field.key]} onChange={e => setDraft({ ...form, [field.key]: Number(e.target.value) })}
        className="w-full rounded-md border border-input bg-background px-3 py-2 disabled:opacity-60" />
    </label>
  }
  const invalid = fields.some(f => !Number.isInteger(form[f.key]) || form[f.key] < f.min || form[f.key] > f.max)
  return <Card className="mb-4 p-5">
    <h3 className="mb-4 text-[14px] font-medium"><SettingsHelp title="上下文与会话记忆">模型能力在 API 模型编辑中设置。这里管理默认窗口和资料分配；实际输入采用估算，实际输出额度可在对话的上下文使用情况中查看。保存后新提问生效，正在生成的回答沿用原预算。</SettingsHelp></h3>
    {input(fields[0])}
    <details className="mt-4"><summary className="cursor-pointer text-[13px] font-medium">高级预算</summary><div className="mt-3 grid gap-4 sm:grid-cols-2">{fields.slice(1).map(input)}</div></details>
    <div className="mt-4 flex justify-end gap-3">
      <Button size="sm" disabled={mutation.isPending || invalid || !draft} onClick={() => {
        const { context_window_tokens, answer_reserve_tokens, history_tokens, summary_input_tokens, summary_tokens } = form
        const values = { context_window_tokens, answer_reserve_tokens, history_tokens, summary_input_tokens, summary_tokens }
        mutation.mutate(values)
      }}>{mutation.isPending ? '保存中…' : '保存'}</Button>
    </div>
  </Card>
}
