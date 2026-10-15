import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function SummaryPlanPreview({ kbId, onClose, onStarted }: { kbId: string; onClose: () => void; onStarted: () => void }) {
  const [fullIds, setFullIds] = useState<number[]>([])
  const [budget, setBudget] = useState(40)
  const plan = useQuery({
    queryKey: ['summary-preview', kbId, fullIds],
    queryFn: () => api.kb.globalSummary.preview(kbId, fullIds),
    staleTime: 0,
    retry: false,
  })
  const build = useMutation({
    mutationFn: () => api.kb.globalSummary.buildPlan(kbId, { full_file_ids: fullIds, plan_token: plan.data!.token, budget }),
    onSuccess: onStarted,
  })
  const labels: Record<string, string> = { reuse: '复用完整摘要', reuse_overview: '复用概览', full: '完整摘要', overview: '生成概览 · 非全文覆盖', unavailable: '索引不可用' }
  return <div className="mb-4 space-y-3 rounded-xl border bg-muted/20 p-4 text-xs">
    <div className="flex items-center justify-between"><h3 className="font-medium text-sm">生成前预览</h3><button disabled={build.isPending} onClick={onClose} className="text-muted-foreground">关闭</button></div>
    <p className="text-muted-foreground">预览不调用模型。优先复用已有结果；超大文档默认生成概览，可单独选择完整摘要。</p>
    {plan.isFetching ? <p>正在检查索引与缓存…</p> : plan.data && <>
      <div className="max-h-72 space-y-2 overflow-y-auto">
        {plan.data.documents.map(doc => <div key={doc.file_id} className="rounded-lg border bg-background px-3 py-2">
          <div className="flex flex-wrap items-start justify-between gap-2"><span className="min-w-0 break-all font-medium" title={doc.name}>{doc.name.split(/[\\/]/).pop()}</span><span className="shrink-0 text-muted-foreground">{labels[doc.action]}</span></div>
          <div className="mt-1 text-muted-foreground">{doc.chars > 0 && `${doc.chars.toLocaleString()} 字符 · `}预计新增 {doc.estimated_calls} 次调用{doc.cached_segments > 0 && ` · 已缓存 ${doc.cached_segments} 段笔记，完整处理时复用`}</div>
          {doc.error && <p className="mt-1 text-destructive">{doc.error}</p>}
          {['overview', 'reuse_overview'].includes(doc.action) || fullIds.includes(doc.file_id) ? <label className="mt-2 flex items-center gap-2"><input type="checkbox" checked={fullIds.includes(doc.file_id)} disabled={build.isPending} onChange={e => setFullIds(ids => e.target.checked ? [...ids, doc.file_id].sort((a,b) => a-b) : ids.filter(id => id !== doc.file_id))} />改为完整摘要（可能产生大量请求）</label> : null}
        </div>)}
      </div>
      <p>含最终汇总，预计 <strong>{plan.data.estimated_calls}</strong> 次调用。此为估算，输出长度及重试会影响实际用量；不含向量化请求。单次任务默认最多运行 60 分钟，未完成部分可继续。</p>
      <label className="flex flex-wrap items-center gap-2">本次模型调用上限<input aria-label="本次模型调用上限" className="w-24 rounded border bg-background px-2 py-1" type="number" min={1} max={1000} value={budget} disabled={build.isPending} onChange={e => setBudget(Number(e.target.value))} /><span className="text-muted-foreground">达到上限停止，保留已完成结果；网络重试另有次数限制。</span></label>
      <p className="text-muted-foreground">概览使用结构统计与有限原文窗口，不保证覆盖所有类别和异常。已有历史摘要将继续复用，其完整性无法追溯确认。</p>
      {plan.data.estimated_calls > budget && <p className="text-amber-600">预计调用量超过预算。请选择概览，或确认成本后提高上限。</p>}
      <button className="rounded-md bg-primary px-3 py-2 text-primary-foreground disabled:opacity-50" disabled={build.isPending || plan.isFetching || plan.data.blocked || !Number.isInteger(budget) || budget < plan.data.estimated_calls || budget > 1000} onClick={() => build.mutate()}>{build.isPending ? '正在创建任务…' : '确认并开始生成'}</button>
    </>}
    {(plan.error || build.error) && <div role="alert" className="text-destructive">{(plan.error || build.error)?.message}<button className="ml-2 underline" onClick={() => { build.reset(); void plan.refetch() }}>重新预览</button></div>}
  </div>
}
