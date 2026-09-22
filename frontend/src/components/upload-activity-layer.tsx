import { UploadElapsed } from '@/components/upload-elapsed'
import { uploadProgressView } from '@/lib/upload-progress'
/**
 * 全局上传活动层：
 * - 右下角悬浮进度条：任何页面都能看到进行中的导入/上传，点击打开详情弹窗
 * - 完成时：弹窗没开着 → toast + 浏览器系统通知（页面在后台也能收到）
 * - 「查看」入口：banner 和悬浮条共用 useUploadUiStore.viewTask
 */
import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Loader2, UploadCloud } from 'lucide-react'

import { api, type UploadTask } from '@/lib/api'
import { BatchUploadDialog } from '@/components/batch-upload-dialog'
import { useUploadUiStore } from '@/stores/upload-ui'
import { useUploadRunner } from '@/stores/upload-runner'

const POLL_MS = 3000

export function UploadActivityLayer() {
  const qc = useQueryClient()
  const viewTask = useUploadUiStore((s) => s.viewTask)
  const clearView = useUploadUiStore((s) => s.clearView)
  const openView = useUploadUiStore((s) => s.openView)

  const { data } = useQuery({
    queryKey: ['upload-tasks', 'active'],
    queryFn: () => api.knowledge.uploadTasksActive(),
    refetchInterval: POLL_MS,
  })

  // KB 名映射（弹窗标题用）
  const kbsQuery = useQuery({
    queryKey: ['kbs'],
    queryFn: () => api.kb.list(),
    staleTime: 60_000,
    enabled: (data?.tasks?.length ?? 0) > 0 || !!viewTask,
  })
  const kbNameById = new Map((kbsQuery.data ?? []).map((k) => [k.id, k.name]))

  // 完成检测：上一轮还在、这一轮消失的任务 → 查终态，completed 才提示
  const prevIdsRef = useRef<Set<string> | null>(null)
  useEffect(() => {
    const tasks = data?.tasks ?? []
    const ids = new Set(tasks.map((t) => t.id))
    const prev = prevIdsRef.current
    if (prev) {
      for (const gone of prev) {
        if (ids.has(gone)) continue
        api.knowledge
          .uploadTask(gone)
          .then((d) => {
            if (d.status !== 'completed') return
            if (useUploadUiStore.getState().foregroundTaskId === gone) return // 弹窗开着，弹窗自己提示
            useUploadRunner.getState().markSeen()
            const n = d.done + d.skipped
            const suffix = d.failed > 0 ? `，${d.failed} 个失败` : ''
            toast.success(`后台导入完成：${n} 个文件已入库${suffix}`)
            if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
              try {
                new Notification('Insight 批量导入完成', {
                  body: `${n} 个文件已入库${suffix}`,
                })
              } catch {
                // 某些环境 Notification 构造受限，忽略
              }
            }
            qc.invalidateQueries()
          })
          .catch(() => {})
      }
    }
    prevIdsRef.current = ids
  }, [data, qc])

  const active = (data?.tasks ?? []).filter(
    (t) => t.status === 'running' || t.status === 'uploading' || t.status === 'paused' || t.status === 'cancelling' || t.status === 'cleaning' || t.status === 'cleanup_failed',
  )

  return (
    <>
      {active.length > 0 && !viewTask ? (
        <div className="fixed bottom-4 right-4 z-40 flex flex-col gap-2">
          {active.map((t) => (
            <Pill key={t.id} task={t} onClick={() => openView(t.id, t.kb_id)} />
          ))}
        </div>
      ) : null}

      {viewTask ? (
        <BatchUploadDialog
          open
          onClose={clearView}
          kbId={viewTask.kbId}
          kbName={kbNameById.get(viewTask.kbId) ?? '...'}
          initialTaskId={viewTask.taskId}
          onCompleted={() => qc.invalidateQueries()}
        />
      ) : null}
    </>
  )
}

function Pill({ task, onClick }: { task: UploadTask; onClick: () => void }) {
  const finished = task.done + task.skipped + task.failed
  const isUploading = task.status === 'uploading'
  const view = uploadProgressView(task)
  const pct = view.percent
  const current = task.current_file_path

  return (
    <button
      onClick={onClick}
      className="group flex w-[280px] flex-col gap-1.5 rounded-lg border bg-background/95 p-3 text-left shadow-lg backdrop-blur transition-colors hover:border-primary/50"
      title={current ?? undefined}
    >
      <div className="flex items-center gap-2 text-[12px]">
        {isUploading ? (
          <UploadCloud className="h-3.5 w-3.5 shrink-0 text-primary" />
        ) : (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
        )}
        <span className="flex-1 truncate font-medium">
          {view.label}{task.total > 1 ? ` · ${finished}/${task.total}` : ''}
        </span>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          {pct == null ? '' : `${pct}%`}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-all"
          style={{ width: pct == null ? '35%' : `${pct}%`, opacity: pct == null ? 0.35 : 1 }}
        />
      </div>
      <UploadElapsed task={task} />
      {task.current_detail && <div className="break-words text-[11px] text-muted-foreground">{task.current_detail}</div>}
      {current && !isUploading ? (
        <div className="truncate font-mono text-[10px] text-muted-foreground">{current}</div>
      ) : null}
    </button>
  )
}
