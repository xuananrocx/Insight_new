import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Sparkles } from 'lucide-react'
import { api, type SummaryConfig } from '@/lib/api'
import { Card } from '@/components/ui/card'

const fields = [
  ['output_tokens', '初始输出额度', 'tokens · 包括服务商计入的推理输出'],
  ['output_token_limit', '补偿输出上限', 'tokens · 截断时最多增额一次，须由模型支持'],
  ['step_timeout_seconds', '单步骤时长', '秒 · 包含网络重试'],
  ['task_timeout_seconds', '整个任务时长', '秒 · 达到预算后保留已完成结果'],
  ['request_limit', '默认请求上限', '次 · 包含重试和生成补偿，缓存不计数'],
] as const
const advanced = [
  ['segment_chars', '文档分段大小', '字符 · 大文档按章节分段，不丢弃正文'],
  ['reduce_chars', '汇总分组大小', '字符 · 预览与执行使用相同策略'],
  ['auto_full_calls', '自动完整摘要阈值', '预计调用次数 · 超出后默认提供文档概览'],
  ['retry_count', '网络重试次数', '每步骤 · 不自动切换 API 或模型'],
  ['split_depth', '超时拆分层数', '仅针对收到 504 的汇总组，0 表示关闭'],
  ['checkpoint_retention_days', '中间结果保留时间', '天 · 最近复用时续期，不删除正式文档摘要'],
  ['min_word_count', '导入摘要最低字数', '字 · 仅影响导入时的自动摘要'],
] as const

export function AiSummaryConfigCard() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['ai-summary-config'], queryFn: api.aiSummary.get })
  const [draft, setDraft] = useState<SummaryConfig | null>(null)
  const form = draft ?? query.data
  const mutation = useMutation({
    mutationFn: api.aiSummary.update,
    onSuccess: data => {
      qc.setQueryData(['ai-summary-config'], data)
      void qc.invalidateQueries({ queryKey: ['summary-preview'] })
      setDraft(null)
    },
  })
  const edit = <K extends keyof SummaryConfig>(key: K, value: SummaryConfig[K]) => {
    if (form) setDraft({ ...form, [key]: value })
  }
  const numeric = (entries: readonly (readonly [keyof SummaryConfig, string, string])[]) => entries.map(([key, label, hint]) => {
    const schema = query.data?.schema[key]
    return <label key={key} className="space-y-1.5 text-xs">
      <span className="block font-medium">{label}</span>
      <input type="number" value={Number(form?.[key])} min={schema?.minimum} max={schema?.maximum}
        disabled={mutation.isPending || query.data?.sources[key]?.endsWith('覆盖')} onChange={e => edit(key, Number(e.target.value))}
        className="h-9 w-full rounded-md border bg-background px-3" />
      <span className="block text-[11px] leading-relaxed text-muted-foreground">{hint} · {query.data?.sources[key]}</span>
    </label>
  })
  return <Card className="mb-4 p-5">
    <div className="mb-2 flex items-center gap-2"><Sparkles className="h-4 w-4 text-muted-foreground" /><h3 className="text-sm font-medium">文档摘要与全局概览</h3></div>
    <p className="mb-4 text-xs leading-relaxed text-muted-foreground">复用有效摘要与已完成汇总。保存后对新任务生效，运行中的任务继续使用启动时的配置。输出额度需符合所选模型能力。</p>
    {query.isLoading && <p className="text-xs">正在读取配置…</p>}
    {form && <>
      <div className="grid gap-4 sm:grid-cols-2">{numeric(fields)}</div>
      <details className="mt-5 rounded-lg border p-3">
        <summary className="cursor-pointer text-xs font-medium">高级设置与导入行为</summary>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div className="space-y-2 text-xs"><span className="block font-medium">摘要网络读取等待</span>
            <label className="flex items-center gap-2"><input type="checkbox" checked={form.read_timeout_seconds === null} disabled={mutation.isPending}
              onChange={e => edit('read_timeout_seconds', e.target.checked ? null : query.data!.effective_read_timeout_seconds)} />继承通用连接配置</label>
            {form.read_timeout_seconds !== null && <input type="number" aria-label="摘要网络读取等待（秒）" min={query.data?.schema.read_timeout_seconds?.anyOf?.[0]?.minimum} max={query.data?.schema.read_timeout_seconds?.anyOf?.[0]?.maximum} value={form.read_timeout_seconds} disabled={mutation.isPending}
              onChange={e => edit('read_timeout_seconds', Number(e.target.value))} className="h-9 w-full rounded-md border bg-background px-3" />}
            <span className="block text-[11px] text-muted-foreground">已保存的生效值：{query.data?.effective_read_timeout_seconds} 秒；指网络无数据等待，不是任务总时长。</span>
          </div>
          {numeric(advanced)}
        </div>
        <label className="mt-4 flex items-center gap-2 text-xs"><input type="checkbox" checked={form.enabled} disabled={mutation.isPending} onChange={e => edit('enabled', e.target.checked)} />导入时自动生成摘要与概念</label>
        <p className="mt-1 text-[11px] text-muted-foreground">此开关不影响手动生成全局概览。大文档可能需要多次调用，费用由所选模型决定。</p>
      </details>
      <div className="mt-4 flex items-center justify-between gap-3">
        <button className="text-xs text-muted-foreground underline" disabled={mutation.isPending} onClick={() => setDraft(Object.fromEntries(Object.entries(query.data!.defaults).map(([key, value]) => [key, query.data!.sources[key]?.endsWith('覆盖') ? form[key as keyof SummaryConfig] : value])) as SummaryConfig)}>恢复默认值（保存后生效）</button>
        <button disabled={!draft || mutation.isPending} onClick={() => mutation.mutate(form)} className="rounded-md bg-primary px-4 py-2 text-xs text-primary-foreground disabled:opacity-50">{mutation.isPending ? '保存中…' : '保存摘要配置'}</button>
      </div>
    </>}
    {(query.error || mutation.error) && <p role="alert" className="mt-3 text-xs text-destructive">{(query.error || mutation.error)?.message}</p>}
    {mutation.isSuccess && !draft && <p role="status" className="mt-3 text-xs text-muted-foreground">已保存，新任务使用此配置。</p>}
  </Card>
}
