import { AlertTriangle, Loader2, X } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import type { EmbeddingPrecheckResult } from '@/lib/api'

function formatEstimate(seconds: number): string {
  if (seconds < 60) return `约 ${seconds} 秒`
  const m = Math.ceil(seconds / 60)
  return `约 ${m} 分钟`
}

export function RebuildConfirmModal({
  modelLabel,
  precheck,
  onCancel,
  onConfirm,
}: {
  modelLabel: string
  precheck: EmbeddingPrecheckResult
  onCancel: () => void
  onConfirm: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-lg border border-input bg-card p-5 shadow-lg">
        <div className="mb-3 flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-warning/15">
            <AlertTriangle className="h-4 w-4 text-warning" />
          </div>
          <div className="flex-1">
            <h3 className="text-[14px] font-semibold">切换到 {modelLabel} 需要重建向量库</h3>
            <p className="mt-1 text-[12px] text-muted-foreground">
              Embedding 维度变化（{precheck.current_dim} → {precheck.new_dim}），现有向量库无法继续使用。
            </p>
          </div>
          <button onClick={onCancel} className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="mb-4 rounded-md border border-border/40 bg-muted/30 p-3 text-[12px]">
          <div className="mb-1.5 flex justify-between">
            <span className="text-muted-foreground">受影响文档</span>
            <span className="font-medium tabular-nums">{precheck.doc_count} 个</span>
          </div>
          <div className="mb-1.5 flex justify-between">
            <span className="text-muted-foreground">维度变化</span>
            <span className="font-medium tabular-nums">{precheck.current_dim} → {precheck.new_dim}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">预计耗时</span>
            <span className="font-medium tabular-nums">{formatEstimate(precheck.est_seconds)}</span>
          </div>
        </div>

        <div className="mb-4 rounded-md bg-warning/10 px-3 py-2 text-[11px] text-warning">
          ⚠️ 重建期间问答功能暂停。失败时会自动回滚到当前模型。
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={confirming}>
            取消
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setConfirming(true)
              onConfirm()
            }}
            disabled={confirming}
          >
            {confirming ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
            {confirming ? '启动中...' : '确认重建并切换'}
          </Button>
        </div>
      </div>
    </div>
  )
}
