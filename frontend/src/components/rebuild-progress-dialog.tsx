import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { EmbeddingRebuildStatus } from '@/lib/api'
import { api } from '@/lib/api'

function elapsed(startedAt: number | null): number {
  if (!startedAt) return 0
  return Math.round((Date.now() / 1000 - startedAt))
}

export function RebuildProgressDialog({
  modelName,
  total,
  onComplete,
}: {
  modelName: string
  total: number
  onComplete: (status: EmbeddingRebuildStatus) => void
}) {
  const [status, setStatus] = useState<EmbeddingRebuildStatus | null>(null)
  const completedRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const s = await api.embedding.rebuildStatus()
        if (cancelled) return
        setStatus(s)
        if ((s.status === 'succeeded' || s.status === 'failed') && !completedRef.current) {
          completedRef.current = true
          setTimeout(() => onComplete(s), 1500)
        }
      } catch {
        // ignore transient errors
      }
    }
    poll()
    const id = setInterval(poll, 1500)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [onComplete])

  const current = status?.current ?? 0
  const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0
  const isRunning = status?.status === 'in_progress'
  const isSucceeded = status?.status === 'succeeded'
  const isFailed = status?.status === 'failed'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-lg border border-input bg-card p-5 shadow-lg">
        <div className="mb-3 flex items-start gap-3">
          <div className="mt-0.5">
            {isRunning ? <Loader2 className="h-5 w-5 animate-spin text-primary" />
              : isSucceeded ? <CheckCircle2 className="h-5 w-5 text-success" />
              : isFailed ? <AlertCircle className="h-5 w-5 text-destructive" />
              : <Loader2 className="h-5 w-5 animate-spin text-primary" />}
          </div>
          <div className="flex-1">
            <h3 className="text-[14px] font-semibold">
              {isSucceeded ? '重建完成' : isFailed ? '重建失败' : '正在重建向量库'}
            </h3>
            <p className="mt-0.5 text-[12px] text-muted-foreground">
              {isSucceeded ? `已切换到 ${modelName}` : isFailed ? '已自动回滚' : `切换到 ${modelName}`}
            </p>
          </div>
        </div>

        <div className="mb-3">
          <div className="mb-1 flex justify-between text-[11px] text-muted-foreground">
            <span className="truncate">{status?.stage || '准备中...'}</span>
            <span className="tabular-nums">{current} / {total}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className={`h-full rounded-full transition-all ${isFailed ? 'bg-destructive' : isSucceeded ? 'bg-success' : 'bg-primary'}`}
              style={{ width: `${pct}%` }}
            />
          </div>
          {isRunning ? (
            <div className="mt-1 text-right text-[10px] text-muted-foreground/70 tabular-nums">
              已用 {elapsed(status?.started_at ?? null)}s
            </div>
          ) : null}
        </div>

        {isFailed && status?.error ? (
          <div className="mb-3 rounded-md bg-destructive/10 px-3 py-2 text-[11px] text-destructive">
            {status.error}
          </div>
        ) : null}

        {isRunning ? (
          <div className="text-center text-[11px] text-muted-foreground">
            请勿关闭页面，问答功能已暂停
          </div>
        ) : null}

        {(isSucceeded || isFailed) && !completedRef.current ? null : null}
      </div>
    </div>
  )
}
