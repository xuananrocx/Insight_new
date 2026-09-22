import { uploadProgressView } from '@/lib/upload-progress'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertCircle, Eye, Loader2, Play, Trash2 } from 'lucide-react'

import { api, type UploadTask } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { useUploadUiStore } from '@/stores/upload-ui'

/**
 * 未完成批量上传任务 banner：
 *
 * - paused（进程重启/上传中断留下的）：显示"继续 / 放弃"
 * - running：真实进度 + 「查看」打开详情弹窗
 * - uploading：分批上传进行中（悬浮条也会显示）
 */
export function InterruptedTasksBanner() {
  const qc = useQueryClient()
  const openView = useUploadUiStore((s) => s.openView)
  const { data, isLoading } = useQuery({
    queryKey: ['upload-tasks', 'active'],
    queryFn: () => api.knowledge.uploadTasksActive(),
    refetchInterval: 5000,
  })

  const resumeMutation = useMutation({
    mutationFn: (taskId: string) => api.knowledge.resumeUploadTask(taskId),
    onSuccess: () => {
      toast.success('已恢复任务')
      qc.invalidateQueries({ queryKey: ['upload-tasks', 'active'] })
    },
    onError: (e: Error) => toast.error(`恢复失败：${e.message}`),
  })

  const deleteMutation = useMutation({
    mutationFn: (taskId: string) => api.knowledge.deleteUploadTask(taskId),
    onSuccess: () => {
      toast.info('已放弃任务')
      qc.invalidateQueries({ queryKey: ['upload-tasks', 'active'] })
    },
    onError: (e: Error) => toast.error(`放弃失败：${e.message}`),
  })

  if (isLoading || !data || data.tasks.length === 0) return null

  return (
    <div className="space-y-2">
      {data.tasks.map((task) => (
        <TaskRow
          key={task.id}
          task={task}
          onResume={() => resumeMutation.mutate(task.id)}
          onDelete={() => deleteMutation.mutate(task.id)}
          onView={() => openView(task.id, task.kb_id)}
          resuming={resumeMutation.isPending}
          deleting={deleteMutation.isPending}
        />
      ))}
    </div>
  )
}

type TaskRowProps = {
  task: UploadTask
  onResume: () => void
  onDelete: () => void
  onView: () => void
  resuming: boolean
  deleting: boolean
}

function TaskRow({ task, onResume, onDelete, onView, resuming, deleting }: TaskRowProps) {
  const finished = task.done + task.skipped + task.failed
  const isPaused = task.status === 'paused'
  const isRunning = task.status === 'running'
  const isUploading = task.status === 'uploading'

  return (
    <div className="flex items-center gap-3 rounded-md border border-warning/40 bg-warning/5 px-3 py-2 text-[12px]">
      <AlertCircle className="h-4 w-4 shrink-0 text-warning" />
      <div className="flex-1">
        {isPaused ? (
          task.current_stage === 'upload_interrupted' ? (
            <span>
              上次上传中断：已接收 {task.total} 个文件
              <span className="ml-1 text-muted-foreground">
                （继续 = 处理已接收部分，放弃 = 删除任务）
              </span>
            </span>
          ) : (
            <span>
              上次批量导入未完成：{finished}/{task.total}
              <span className="ml-1 text-muted-foreground">
                （进程重启后自动暂停）
              </span>
            </span>
          )
        ) : isRunning ? (
          <span>
            <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />
            后台导入中：{finished}/{task.total}
          </span>
        ) : isUploading ? (
          <span>
            <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />
            分批上传中：已接收 {task.total} 个文件
          </span>
        ) : <span>{uploadProgressView(task).label}</span>}
      </div>
      {isRunning || isUploading || ['cancelling', 'cleaning', 'cleanup_failed'].includes(task.status) ? (
        <Button
          variant="outline"
          size="sm"
          className="h-7 gap-1 text-[11px]"
          onClick={onView}
        >
          <Eye className="h-3 w-3" />
          查看
        </Button>
      ) : null}
      {isPaused ? (
        <>
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1 text-[11px]"
            onClick={onResume}
            disabled={resuming || deleting}
          >
            {resuming ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Play className="h-3 w-3" />
            )}
            继续
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-[11px]"
            onClick={onDelete}
            disabled={resuming || deleting}
          >
            {deleting ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Trash2 className="h-3 w-3" />
            )}
            放弃
          </Button>
        </>
      ) : null}
    </div>
  )
}
