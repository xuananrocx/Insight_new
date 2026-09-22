import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Globe, RefreshCw, Trash2, Sparkles, AlertCircle, AlertTriangle } from 'lucide-react'

import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'

interface Props {
  kbId: string
  canManage?: boolean
}

export function KbGlobalSummaryCard({ kbId, canManage = false }: Props) {
  const queryClient = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  useEffect(() => { setConfirmDelete(false) }, [kbId, canManage])

  const query = useQuery({
    queryKey: ['kb-global-summary', kbId],
    queryFn: () => api.kb.globalSummary.get(kbId),
    enabled: !!kbId,
  })

  const buildMutation = useMutation({
    mutationFn: (force: boolean) => {
      if (!canManage) throw new Error('没有管理此知识库的权限')
      return api.kb.globalSummary.build(kbId, force)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kb-global-summary', kbId] })
    },
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

  return (
    <Card className="mb-6 overflow-hidden">
      <div className="flex items-center justify-between border-b px-5 py-3">
        <div className="flex items-center gap-2">
          <Globe className="h-4 w-4 text-muted-foreground" />
          <span className="text-[14px] font-medium">KB 全局概览</span>
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            迭代 6
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
                onClick={() => buildMutation.mutate(true)}
                disabled={buildMutation.isPending || deleteMutation.isPending}
                className="inline-flex items-center gap-1 rounded border bg-background px-2 py-1 text-[11px] font-medium hover:bg-accent/30 disabled:opacity-50"
                title="强制重新生成（基于最新文档摘要）"
              >
                <RefreshCw className={cn('h-3 w-3', buildMutation.isPending && 'animate-spin')} />
                {buildMutation.isPending ? '生成中...' : '重新生成'}
              </button>
              {confirmDelete ? (
                <>
                  <button
                    type="button"
                    onClick={() => deleteMutation.mutate()}
                    disabled={buildMutation.isPending || deleteMutation.isPending}
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
                  disabled={buildMutation.isPending || deleteMutation.isPending}
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
              onClick={() => buildMutation.mutate(false)}
              disabled={buildMutation.isPending || deleteMutation.isPending}
              className="inline-flex items-center gap-1 rounded border border-primary bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              <Sparkles className="h-3 w-3" />
              {buildMutation.isPending ? '生成中...' : '生成全局摘要'}
            </button>
          )}
        </div>}
      </div>

      <div className="px-5 py-4">
        {(query.error || buildMutation.error || deleteMutation.error) && <p role="alert" className="mb-3 text-xs text-destructive">{(query.error || buildMutation.error || deleteMutation.error)?.message}</p>}
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
              {data.summary}
            </div>
            {data.summary.length > 300 ? (
              <button
                type="button"
                onClick={() => setExpanded(!expanded)}
                className="mt-2 text-[11px] font-medium text-primary hover:underline"
              >
                {expanded ? '收起' : '展开全文'}
              </button>
            ) : null}
            <div className="mt-3 rounded-md bg-muted/30 px-3 py-2 text-[10px] leading-relaxed text-muted-foreground">
              <span className="font-medium text-foreground">用途：</span>{' '}
              summary/agentic 检索策略下会作为 KB 整体背景注入 prompt 顶部，
              帮助回答"这个系统是什么"、"整体架构如何"等全局问题。
            </div>
          </div>
        ) : (
          <div className="py-6 text-center text-[12px] text-muted-foreground">
            <AlertCircle className="mx-auto mb-2 h-5 w-5 opacity-40" />
            还没有 KB 全局摘要。
            <div className="mt-1 text-[10px]">
              {canManage ? '点击右上角“生成全局摘要”按钮（基于已投喂的文档摘要）。需要先在投喂时启用 AI 摘要。' : '请联系知识库所有者或具有管理权限的用户生成概览。'}
            </div>
          </div>
        )}
      </div>
    </Card>
  )
}
