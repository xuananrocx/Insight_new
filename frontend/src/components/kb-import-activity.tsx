/**
 * KB Pack 导入 + 文档索引建设的全局活动面板：
 * - 展开态：居中弹窗（与导入预检弹窗同款），点「后台运行」收起
 * - 收起态：右下角悬浮条，点击重新展开为弹窗
 * - 状态来自全局 store（页面切换/登出重登不中断导入）
 * - 导入中取消 = 回滚整个导入；索引建设取消 = 安全取消（保留已完成）
 */
import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Loader2, X } from 'lucide-react'

import { useKbImportStore } from '@/stores/kb-import'
import { Button } from '@/components/ui/button'
import { ManagementDialog } from '@/components/management-dialog'

const stageLabels: Record<string, string> = {
  starting: '准备中',
  creating_kb: '创建知识库',
  extracting_documents: '解压文档',
  importing_vectors: '导入向量',
  rebuilding_embeddings: '重建向量',
  done: '已完成',
}

const indexLabels: Record<string, string> = {
  queued: '排队中',
  running: '建设中',
  cancelling: '正在取消',
}

function ProgressBar({ pct }: { pct: number | null }) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
      <div
        className="h-full rounded-full bg-primary transition-all"
        style={{ width: pct == null ? '35%' : `${pct}%`, opacity: pct == null ? 0.35 : 1 }}
      />
    </div>
  )
}

export function KbImportPill() {
  const {
    phase, cancelling, expanded, kbName, stage, current, total, file, index,
    cancel, expand, collapse, dismiss,
  } = useKbImportStore()
  const qc = useQueryClient()
  const prevPhase = useRef(phase)

  useEffect(() => {
    if (prevPhase.current !== phase) {
      qc.invalidateQueries({ queryKey: ['kbs'] })
    }
    prevPhase.current = phase
  }, [phase, qc])

  if (phase === 'idle') return null

  const importPct = total > 0 && (stage === 'extracting_documents' || stage === 'rebuilding_embeddings')
    ? Math.round((current / total) * 100)
    : null
  const title = kbName || '导入知识库'
  const indexPct = index && index.total > 0 ? Math.round((index.completed / index.total) * 100) : null

  if (expanded && (phase === 'importing' || phase === 'indexing')) {
    if (phase === 'importing') {
      return (
        <ManagementDialog title="正在导入知识库" onClose={collapse} busy className="max-w-lg">
          <h2 className="mb-2 text-lg font-semibold">{title}</h2>
          <div className="space-y-2 rounded-md border bg-muted/20 p-3 text-[12px]">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {cancelling ? '正在取消，回滚已导入数据…' : stageLabels[stage] ?? '导入中'}
              </span>
              {importPct != null && <span>{current}/{total}（{importPct}%）</span>}
            </div>
            <ProgressBar pct={importPct} />
            {file && !cancelling && (
              <div className="truncate pt-1 font-mono text-[10px] text-muted-foreground" title={file}>{file}</div>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="destructive" size="sm" disabled={cancelling} onClick={cancel}>
              取消导入
            </Button>
            <Button size="sm" disabled={cancelling} onClick={collapse}>
              后台运行
            </Button>
          </div>
        </ManagementDialog>
      )
    }
    return (
      <ManagementDialog title="文档索引建设中" onClose={collapse} className="max-w-lg">
        <h2 className="mb-2 text-lg font-semibold">{title}</h2>
        <div className="mb-3 flex items-center gap-2 rounded-md border border-success/40 bg-success/10 p-3 text-[12px] text-success">
          <CheckCircle2 className="h-4 w-4 shrink-0" />
          <span>知识库已导入完成，正在构建文档索引</span>
        </div>
        <div className="space-y-2 rounded-md border bg-muted/20 p-3 text-[12px]">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">
              文档索引{indexLabels[index?.state ?? 'running'] ?? '建设中'}{cancelling ? ' · 已请求取消' : ''}
            </span>
            {index && index.total > 0 && <span>{index.completed}/{index.total}（{indexPct}%）</span>}
          </div>
          <ProgressBar pct={indexPct} />
          {index?.detail && (
            <div className="truncate pt-1 text-[10px] text-muted-foreground" title={index.detail}>{index.detail}</div>
          )}
        </div>
        <div className="flex justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!index?.taskId || index.state === 'cancelling'}
            onClick={cancel}
          >
            安全取消
          </Button>
          <Button size="sm" onClick={collapse}>后台运行</Button>
        </div>
      </ManagementDialog>
    )
  }

  if (phase === 'done') {
    const indexFailed = index && index.failed > 0
    return (
      <div className="flex w-[280px] flex-col gap-1 rounded-lg border bg-background/95 p-3 shadow-lg backdrop-blur">
        <div className="flex items-center gap-2 text-[12px]">
          {indexFailed ? (
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" />
          )}
          <span className="flex-1 truncate font-medium">{title}</span>
          <button
            type="button"
            className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
            aria-label="关闭"
            onClick={dismiss}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className={`text-[11px] ${indexFailed ? 'text-warning' : 'text-muted-foreground'}`}>
          {indexFailed
            ? `文档索引建设未完成：${index!.failed} 份失败，可在知识库详情重试`
            : '导入与文档索引建设已完成'}
        </div>
      </div>
    )
  }

  const label = phase === 'importing'
    ? (cancelling ? '正在取消' : stageLabels[stage] ?? '导入中')
    : `文档索引${indexLabels[index?.state ?? 'running'] ?? '建设中'}${index && index.total ? ` ${index.completed}/${index.total}` : ''}`
  return (
    <button
      onClick={expand}
      className="group flex w-[280px] flex-col gap-1.5 rounded-lg border bg-background/95 p-3 text-left shadow-lg backdrop-blur transition-colors hover:border-primary/50"
      title="点击展开详情"
    >
      <div className="flex items-center gap-2 text-[12px]">
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
        <span className="flex-1 truncate font-medium">{title} · {label}</span>
      </div>
      {phase === 'importing' && <ProgressBar pct={importPct} />}
      {phase === 'indexing' && indexPct != null && <ProgressBar pct={indexPct} />}
    </button>
  )
}
