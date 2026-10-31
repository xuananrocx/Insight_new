import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Globe, RefreshCw, Trash2, Sparkles, AlertCircle, AlertTriangle } from 'lucide-react'

import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { SummaryPlanPreview } from './summary-plan-preview'

interface Props {
  kbId: string
  canManage?: boolean
}

export function KbGlobalSummaryCard({ kbId, canManage = false }: Props) {
  const queryClient = useQueryClient()
  const [previewKb, setPreviewKb] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [deleteConfirmation, setDeleteConfirmation] = useState<{ kbId: string; canManage: boolean; open: boolean }>({ kbId, canManage, open: false })
  if (deleteConfirmation.kbId !== kbId || deleteConfirmation.canManage !== canManage) {
    setDeleteConfirmation({ kbId, canManage, open: false })
  }
  const confirmDelete = deleteConfirmation.kbId === kbId && canManage && deleteConfirmation.open
  const setConfirmDelete = (open: boolean) => setDeleteConfirmation({ kbId, canManage, open })

  const query = useQuery({
    queryKey: ['kb-global-summary', kbId],
    queryFn: () => api.kb.globalSummary.get(kbId),
    enabled: !!kbId,
  })

  const taskQuery = useQuery({
    queryKey: ['kb-summary-task', kbId],
    queryFn: () => api.kb.globalSummary.task(kbId),
    enabled: !!kbId,
    refetchInterval: query => ['queued', 'running', 'cancelling'].includes(query.state.data?.task?.state || '') ? 2000 : 10000,
  })
  const task = taskQuery.data?.task
  const taskActive = !!task && ['queued', 'running', 'cancelling'].includes(task.state)
  useEffect(() => {
    if (task?.state === 'done') void queryClient.invalidateQueries({ queryKey: ['kb-global-summary', kbId] })
  }, [task?.id, task?.state, kbId, queryClient])
  const cancelMutation = useMutation({
    mutationFn: () => api.kb.globalSummary.cancel(kbId, task!.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['kb-summary-task', kbId] }),
  })

  const deleteMutation = useMutation({
    mutationFn: () => {
      if (!canManage) throw new Error('没有管理此知识库的权限')
      return api.kb.globalSummary.remove(kbId)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kb-global-summary', kbId] })
      setConfirmDelete(false)
    },
  })

  const data = query.data
  const hasSummary = data?.has_summary ?? false
  // Move only the application-generated notice in the display. Keep the stored
  // overview intact so AI consumers retain the notice when truncating context.
  const coverageMatch = data?.summary?.match(/^【覆盖说明】([^\n]+)(?:\r?\n){2}/)
  const coverageNotice = coverageMatch?.[1] ?? ''
  const summaryBody = coverageMatch ? data!.summary!.slice(coverageMatch[0].length) : data?.summary ?? ''

  return (
    <Card className="glass-content-card mb-6 overflow-hidden">
      <div className="flex items-center justify-between border-b px-5 py-3">
        <div className="flex items-center gap-2">
          <Globe className="h-4 w-4 text-muted-foreground" />
          <span className="text-[14px] font-medium">KB 全局概览</span>
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            文档摘要汇总
          </span>
          {hasSummary && data?.tokens ? (
            <span className="rounded bg-muted/40 px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {data.tokens} tokens · {data.doc_count ?? 0} 文档
            </span>
          ) : null}
          {hasSummary && data?.stale ? (
            <span
              className="inline-flex items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400"
              title="生成摘要后，知识库又投喂了新文件或文件有变更，当前摘要未覆盖这些内容"
            >
              <AlertTriangle className="h-3 w-3" />
              文档已更新，摘要可能过期
            </span>
          ) : null}
        </div>
        {canManage && <div className="flex items-center gap-1">
          {hasSummary ? (
            <>
              <button
                type="button"
                onClick={() => setPreviewKb(kbId)}
                disabled={taskActive || deleteMutation.isPending}
                className="inline-flex items-center gap-1 rounded border bg-background px-2 py-1 text-[11px] font-medium hover:bg-accent/30 disabled:opacity-50"
                title="复用已有有效文档摘要，只补齐缺失或过期部分后重新汇总"
              >
                <RefreshCw className={'h-3 w-3'} />
                {taskActive ? '生成中...' : '重新生成'}
              </button>
              {confirmDelete ? (
                <>
                  <button
                    type="button"
                    onClick={() => deleteMutation.mutate()}
                    disabled={taskActive || deleteMutation.isPending}
                    className="rounded border border-destructive bg-destructive/10 px-2 py-1 text-[11px] font-medium text-destructive hover:bg-destructive/20"
                  >
                    {deleteMutation.isPending ? '删除中...' : '确认删除'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmDelete(false)}
                    className="rounded border bg-background px-2 py-1 text-[11px]"
                  >
                    取消
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmDelete(true)}
                  disabled={taskActive || deleteMutation.isPending}
                  className="inline-flex items-center gap-1 rounded border bg-background px-2 py-1 text-[11px] hover:bg-accent/30"
                  title="删除全局摘要"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              )}
            </>
          ) : (
            <button
              type="button"
              onClick={() => setPreviewKb(kbId)}
              disabled={taskActive || deleteMutation.isPending}
              className="inline-flex items-center gap-1 rounded border border-primary bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              <Sparkles className="h-3 w-3" />
              {taskActive ? '生成中...' : '生成全局摘要'}
            </button>
          )}
        </div>}
      </div>

      <div className="px-5 py-4">
        {canManage && previewKb === kbId && !taskActive && <SummaryPlanPreview key={kbId} kbId={kbId} onClose={() => setPreviewKb(null)} onStarted={() => {
          setPreviewKb(null)
          void queryClient.invalidateQueries({ queryKey: ['kb-summary-task', kbId] })
          void queryClient.invalidateQueries({ queryKey: ['kb-global-summary', kbId] })
        }} />}
        {task && task.state !== 'done' && <div className="mb-3 space-y-2 rounded-lg border p-3 text-xs" aria-live="polite">
          <p>{taskActive ? (task.state === 'queued' ? '等待生成' : task.state === 'cancelling' ? '正在取消' : '正在生成') : task.state === 'failed' ? '生成失败' : task.state === 'interrupted' ? '生成已中断' : '生成已取消'} · 文档 {task.completed}/{task.total} · 复用 {task.reused} 份</p>
          <p className="text-muted-foreground">{task.detail || '检查并复用已有摘要'}{task.model_id ? ` · 模型 ${task.model_id}` : ''}</p>
          {task.request_count !== undefined && <p className="text-muted-foreground">实际请求 {task.request_count}/{task.request_limit} 次（包含重试）</p>}
          <p className="text-muted-foreground">耗时 {Math.round(task.elapsed_seconds / 60)} 分钟。{taskActive ? '可离开页面，后台继续处理。' : '已有有效摘要保留。'}</p>
          {canManage && taskActive && <button className="rounded border px-2 py-1 disabled:opacity-50" disabled={task.state === 'cancelling' || cancelMutation.isPending} onClick={() => cancelMutation.mutate()}>取消生成</button>}
          {canManage && ['failed', 'cancelled', 'interrupted'].includes(task.state) && <button className="rounded border px-2 py-1 disabled:opacity-50" onClick={() => setPreviewKb(kbId)}>预览并继续（复用已完成部分）</button>}
        </div>}
        {(taskQuery.error || cancelMutation.error) && <p role="alert" className="mb-3 text-xs text-destructive">{cancelMutation.error?.message || '任务状态暂时无法读取，后台任务不一定已停止，请稍后刷新。'}</p>}

        {(query.error || deleteMutation.error) && <p role="alert" className="mb-3 text-xs text-destructive">{(query.error || deleteMutation.error)?.message}</p>}
        {query.isLoading ? (
          <div className="py-4 text-center text-[12px] text-muted-foreground">加载中...</div>
        ) : hasSummary && data?.summary ? (
          <div>
            <div
              className={cn(
                'whitespace-pre-wrap text-[12px] leading-relaxed text-foreground/90',
                !expanded && 'line-clamp-6',
              )}
            >
              {summaryBody}
            </div>
            {summaryBody.length > 300 ? (
              <button
                type="button"
                onClick={() => setExpanded(!expanded)}
                className="mt-2 text-[11px] font-medium text-primary hover:underline"
              >
                {expanded ? '收起' : '展开全文'}
              </button>
            ) : null}
            {(data.created_at || data.model) && <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
              {data.created_at && <span>生成于 {new Date(data.created_at).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</span>}
              {data.model && <span>模型 {data.model}</span>}
            </div>}
            <div className="mt-3 text-left text-[10px] leading-relaxed text-muted-foreground">
              <span className="font-medium text-foreground">用途：</span>{' '}
              在支持知识库概览的检索流程中提供整体背景，
              帮助回答"这个系统是什么"、"整体架构如何"等全局问题。
            </div>
            {coverageNotice && <div className="mt-3 border-t pt-3 text-[11px] leading-relaxed text-muted-foreground">
              <div className="mb-1 flex items-center gap-1.5 font-medium"><AlertCircle className="h-3.5 w-3.5 shrink-0" />覆盖说明</div>
              <p className="break-words">{coverageNotice}</p>
            </div>}
          </div>
        ) : (
          <div className="py-6 text-center text-[12px] text-muted-foreground">
            <AlertCircle className="mx-auto mb-2 h-5 w-5 opacity-40" />
            还没有 KB 全局摘要。
            <div className="mt-1 text-[10px]">
              {canManage ? '点击“生成全局摘要”，复用已有有效文档摘要，仅补齐缺失或过期部分。' : '请联系知识库所有者或具有管理权限的用户生成概览。'}
            </div>
          </div>
        )}
      </div>
    </Card>
  )
}
