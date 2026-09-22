import { UploadElapsed } from '@/components/upload-elapsed'
import { uploadProgressView } from '@/lib/upload-progress'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertTriangle,
  CheckCircle2,
  FileUp,
  FolderUp,
  Loader2,
  Minimize2,
  X,
} from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  api,
  type SkipMode,
  type UploadFileStatus,
  type UploadTask,
} from '@/lib/api'
import { useUploadRunner } from '@/stores/upload-runner'
import { useUploadUiStore } from '@/stores/upload-ui'
import { cn, formatBytes } from '@/lib/utils'

type SelectedFile = {
  file: File
  relativePath: string
}

type Props = {
  open: boolean
  onClose: () => void
  kbId: string
  kbName: string
  /** 默认 autoIngest；从 config 读 */
  defaultAutoIngest?: boolean
  /** 直接查看一个进行中的任务（从悬浮条/banner 打开） */
  initialTaskId?: string | null
  /** 任务完成后回调（让父组件刷新文件列表） */
  onCompleted?: () => void
}

/**
 * 批量上传 Dialog：选文件 → 预检同名 → 分批上传 → 实时进度。
 *
 * 内部状态机：
 *   idle (选文件) → uploading (分批传输) → running (ingest 进度) → done (完成)
 *
 * 「转到后台」：关闭弹窗但传输/ingest 继续（传输循环在 upload-runner store 里，
 * ingest 在后端 worker 里），全局悬浮条持续展示进度。
 */
export function BatchUploadDialog({
  open,
  onClose,
  kbId,
  kbName,
  defaultAutoIngest = true,
  initialTaskId,
  onCompleted,
}: Props) {
  const [streamVersion, setStreamVersion] = useState(0)
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'running'>('idle')
  const [selectedFiles, setSelectedFiles] = useState<SelectedFile[]>([])
  const [rejectedFiles, setRejectedFiles] = useState<Array<{ name: string; reason: string }>>([])
  const [skipMode, setSkipMode] = useState<SkipMode>('skip')
  const [autoIngest, setAutoIngest] = useState(defaultAutoIngest)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  // 任务运行状态
  const [taskId, setTaskId] = useState<string | null>(null)
  const [taskSnapshot, setTaskSnapshot] = useState<UploadTask | null>(null)
  const currentFile = taskSnapshot?.current_file_path ?? null
  const currentProgress = taskSnapshot?.current_stage ? {
    percent: taskSnapshot.current_percent ?? null,
    stage: taskSnapshot.current_stage,
    detail: taskSnapshot.current_detail ?? undefined,
  } : null
  const completedCallback = useRef(onCompleted)
  completedCallback.current = onCompleted
  const [fileEvents, setFileEvents] = useState<
    Record<number, { status: UploadFileStatus; relativePath: string; skipReason?: string | null; error?: string | null }>
  >({})

  const runner = useUploadRunner()
  const setForegroundTaskId = useUploadUiStore((s) => s.setForegroundTaskId)

  // 打开时决定进入哪个阶段：
  // - 指定 initialTaskId → 直接看该任务进度
  // - runner 还在传/刚传完没看过 → 接上上传进度
  useEffect(() => {
    if (!open) return
    if (initialTaskId) {
      setTaskId(initialTaskId)
      setPhase('running')
      return
    }
    if (runner.phase === 'uploading' || runner.phase === 'error') {
      setPhase('uploading')
      return
    }
    if (runner.phase === 'finished' && runner.taskId && !runner.seen) {
      setTaskId(runner.taskId)
      setPhase('running')
    }
    // 其余情况保持 idle（新导入）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialTaskId])

  // 关闭时重置本地展示状态（传输循环在 runner store 里，不受影响）
  useEffect(() => {
    if (!open) {
      const t = setTimeout(() => {
        setPhase('idle')
        setSelectedFiles([])
        setRejectedFiles([])
        setTaskId(null)
        setTaskSnapshot(null)
        setFileEvents({})
      }, 200)
      return () => clearTimeout(t)
    }
  }, [open])

  // runner 传输全部完成 → 切到 ingest 进度阶段
  useEffect(() => {
    if (phase === 'uploading' && runner.phase === 'finished' && runner.taskId) {
      setTaskId(runner.taskId)
      setPhase('running')
    }
    if (phase === 'uploading' && runner.phase === 'idle') {
      // 上传被取消
      setPhase('idle')
      setSelectedFiles([])
    }
  }, [phase, runner.phase, runner.taskId])

  // 弹窗展示中的任务登记到 UI store（悬浮条避免重复完成提示）
  useEffect(() => {
    setForegroundTaskId(open && phase === 'running' && taskId ? taskId : null)
  }, [open, phase, taskId, setForegroundTaskId])

  // 预检：拉 KB 现有文件路径
  const existingPaths = useQuery({
    queryKey: ['kb-file-paths', kbId],
    queryFn: () => api.knowledge.filePaths(kbId),
    enabled: open && phase === 'idle' && selectedFiles.length > 0,
    staleTime: 30_000,
  })

  // 拉取支持的扩展名白名单（用于本地预过滤）
  const supportedExtQuery = useQuery({
    queryKey: ['kb-supported-extensions'],
    queryFn: () => api.knowledge.supportedExtensions(),
    enabled: open,
    staleTime: 5 * 60_000,
  })

  const allowedExtSet = useMemo(() => {
    const exts = supportedExtQuery.data?.extensions || []
    return new Set(exts.map((e) => e.toLowerCase()))
  }, [supportedExtQuery.data])

  // 检查单个文件名是否被允许
  const checkExtAllowed = (
    name: string,
  ): { ok: true } | { ok: false; reason: string } => {
    if (!name) return { ok: false, reason: 'empty_filename' }
    const lower = name.toLowerCase()
    // 系统垃圾文件
    const base = lower.split(/[\\/]/).pop() || lower
    if (
      ['.ds_store', 'thumbs.db', 'desktop.ini'].includes(base) ||
      base.startsWith('__macosx')
    ) {
      return { ok: false, reason: 'hidden_system_file' }
    }
    // 双扩展名（.tar.gz 等）
    for (const pkg of ['tar.gz', 'tar.bz2', 'tar.xz']) {
      if (lower.endsWith('.' + pkg)) {
        return allowedExtSet.has(pkg)
          ? { ok: true }
          : { ok: false, reason: 'unsupported_type' }
      }
    }
    // 无扩展名
    if (!base.includes('.')) return { ok: false, reason: 'no_extension' }
    const ext = base.split('.').pop()!
    return allowedExtSet.has(ext)
      ? { ok: true }
      : { ok: false, reason: 'unsupported_type' }
  }

  const conflictingPaths = useMemo(() => {
    if (!existingPaths.data?.paths) return []
    const existingSet = new Set(existingPaths.data.paths)
    return selectedFiles.filter((sf) => existingSet.has(sf.relativePath))
  }, [existingPaths.data, selectedFiles])

  // 提交批量上传（分批，循环在 upload-runner store 里跑，弹窗可中途转后台）
  const startUpload = () => {
    if (selectedFiles.length === 0) return
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {})
    }
    setPhase('uploading')
    void runner.start({
      kbId,
      skipMode,
      autoIngest,
      files: selectedFiles,
    })
  }

  // 订阅任务进度
  useEffect(() => {
    if (!taskId || phase !== 'running' || !open) return
    const ctrl = new AbortController()

    api.knowledge
      .streamUploadTask(
        taskId,
        {
          onSnapshot: (s) => setTaskSnapshot(s),
          onFileStarted: (e) => {
            setTaskSnapshot((prev) => prev ? {...prev, current_file_path: e.relative_path,
              current_stage: 'processing', current_percent: null, current_detail: null} : prev)
          },
          onFileProgress: (e) => {
            setTaskSnapshot((prev) => prev ? {...prev, current_file_path: e.relative_path,
              current_stage: e.stage, current_percent: e.percent, current_detail: e.detail} : prev)
          },
          onFileFinished: (e) => {
            setFileEvents((prev) => ({
              ...prev,
              [e.file_id]: {
                status: e.status,
                relativePath: e.relative_path,
                skipReason: e.skip_reason,
                error: e.error,
              },
            }))
          },
          onTaskCompleted: () => {
            useUploadRunner.getState().markSeen()
            // 刷新父组件文件列表
            completedCallback.current?.()
            toast.success('批量导入完成')
          },
          onTaskCancelled: () => toast.info('已取消'),
          onTaskCrashed: () => toast.error('任务已停止，请查看详情中的恢复或清理提示'),
          onError: (msg) => toast.error(msg),
        },
        ctrl.signal,
      )
      .catch((e: Error) => {
        if (e.name !== 'AbortError') {
          toast.error(`进度订阅失败：${e.message}`)
        }
      })

    return () => ctrl.abort()
  }, [taskId, phase, open, streamVersion])

  // 文件选择处理（本地按扩展名预过滤）
  const handleFiles = (fileList: FileList | null, _isFolder: boolean) => {
    if (!fileList) return
    const items: SelectedFile[] = []
    const newlyRejected: Array<{ name: string; reason: string }> = []
    Array.from(fileList).forEach((f) => {
      // webkitRelativePath 在 input[webkitdirectory] 上有
      // 标准属性 File.webkitRelativePath 也是
      const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
      // 跳过隐藏文件（.DS_Store 等）— 保留向后兼容
      if (f.name.startsWith('.')) {
        newlyRejected.push({ name: rel, reason: 'hidden_system_file' })
        return
      }
      // 扩展名白名单检查（白名单加载前先放行，后端会兜底）
      if (allowedExtSet.size > 0) {
        const result = checkExtAllowed(f.name)
        if (!result.ok) {
          newlyRejected.push({ name: rel, reason: result.reason })
          return
        }
      }
      items.push({ file: f, relativePath: rel })
    })
    if (items.length > 0) {
      setSelectedFiles((prev) => [...prev, ...items])
    }
    if (newlyRejected.length > 0) {
      setRejectedFiles((prev) => [...prev, ...newlyRejected])
      // 折叠展示：超过 20 个只计数
      const preview = newlyRejected.slice(0, 5).map((r) => r.name).join('、')
      toast.info(
        `已排除 ${newlyRejected.length} 个不支持${
          newlyRejected.length > 1 ? '的文件' : ''
        }${preview ? `：${preview}${newlyRejected.length > 5 ? '...' : ''}` : ''}`,
      )
    }
  }

  const removeSelected = (idx: number) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== idx))
  }

  const totalSize = selectedFiles.reduce((a, sf) => a + sf.file.size, 0)

  // 完成统计
  const isCompleted = taskSnapshot?.status === 'completed'

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        // 传输/ingest 阶段关闭 = 转到后台（传输在 runner store、ingest 在后端，都会继续）
        if (!o) onClose()
      }}
    >
      <DialogContent
        className="max-w-2xl"
        aria-describedby={undefined}
        onEscapeKeyDown={(e) => {
          // Radix 默认 Esc 会关 Dialog，这里统一走 onOpenChange（转后台语义）
          if (phase === 'uploading' || phase === 'running') e.preventDefault()
          onClose()
        }}
      >
        <DialogTitle>批量导入到 · {kbName}</DialogTitle>
        <DialogDescription>
          支持选择多个文件或整个文件夹（含子目录），同名文件可跳过或覆盖。
        </DialogDescription>

        {phase === 'idle' ? (
          <div className="space-y-4">
            {/* 选择文件 / 选择文件夹 */}
            <div className="grid grid-cols-2 gap-3">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => {
                  handleFiles(e.target.files, false)
                  e.target.value = ''
                }}
              />
              <input
                ref={folderInputRef}
                type="file"
                className="hidden"
                onChange={(e) => {
                  handleFiles(e.target.files, true)
                  e.target.value = ''
                }}
                // @ts-expect-error webkitdirectory 是非标准但所有现代浏览器都支持
                webkitdirectory=""
                directory=""
                multiple
              />
              <Button
                variant="outline"
                className="h-auto flex-col gap-2 py-6"
                onClick={() => fileInputRef.current?.click()}
              >
                <FileUp className="h-5 w-5" />
                <span className="text-[13px]">选择文件</span>
                <span className="text-[10px] text-muted-foreground">多选 / Ctrl 单选</span>
              </Button>
              <Button
                variant="outline"
                className="h-auto flex-col gap-2 py-6"
                onClick={() => folderInputRef.current?.click()}
              >
                <FolderUp className="h-5 w-5" />
                <span className="text-[13px]">选择文件夹</span>
                <span className="text-[10px] text-muted-foreground">递归含子目录</span>
              </Button>
            </div>

            {/* 已选文件列表 */}
            {selectedFiles.length > 0 ? (
              <div>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[12px] text-muted-foreground">
                    已选择 <strong>{selectedFiles.length}</strong> 个文件 ·{' '}
                    {formatBytes(totalSize)}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-[11px]"
                    onClick={() => setSelectedFiles([])}
                  >
                    清空
                  </Button>
                </div>
                <div className="max-h-44 overflow-y-auto rounded-md border bg-muted/20">
                  {selectedFiles.slice(0, 50).map((sf, idx) => {
                    const isConflict = conflictingPaths.some(
                      (c) => c.relativePath === sf.relativePath,
                    )
                    return (
                      <div
                        key={`${sf.relativePath}-${idx}`}
                        className="flex items-center justify-between px-3 py-1.5 text-[12px] hover:bg-accent/40"
                      >
                        <span
                          className={cn(
                            'flex-1 truncate font-mono',
                            isConflict && 'text-warning',
                          )}
                          title={sf.relativePath}
                        >
                          {isConflict ? '⚠ ' : ''}
                          {sf.relativePath}
                        </span>
                        <span className="ml-2 shrink-0 text-muted-foreground">
                          {formatBytes(sf.file.size)}
                        </span>
                        <button
                          className="ml-2 shrink-0 text-muted-foreground hover:text-foreground"
                          onClick={() => removeSelected(idx)}
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </div>
                    )
                  })}
                  {selectedFiles.length > 50 ? (
                    <div className="border-t px-3 py-1.5 text-center text-[11px] text-muted-foreground">
                      还有 {selectedFiles.length - 50} 个未展示
                    </div>
                  ) : null}
                </div>
              </div>
            ) : null}

            {/* 被排除文件提示 */}
            {rejectedFiles.length > 0 ? (
              <Card className="border-muted/60 bg-muted/20 p-3">
                <div className="mb-1 flex items-center justify-between text-[12px]">
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <X className="h-3.5 w-3.5" />
                    <strong>{rejectedFiles.length}</strong> 个文件因不支持被排除
                  </div>
                  <button
                    className="text-[11px] text-muted-foreground hover:text-foreground"
                    onClick={() => setRejectedFiles([])}
                  >
                    清除提示
                  </button>
                </div>
                <div className="max-h-24 overflow-y-auto text-[11px] text-muted-foreground">
                  {rejectedFiles.slice(0, 8).map((r, idx) => (
                    <div key={`${r.name}-${idx}`} className="truncate font-mono">
                      · {r.name}
                    </div>
                  ))}
                  {rejectedFiles.length > 8
                    ? `... 还有 ${rejectedFiles.length - 8} 个`
                    : null}
                </div>
              </Card>
            ) : null}

            {/* 同名预检提示 */}
            {existingPaths.isLoading ? (
              <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                正在检查同名文件...
              </div>
            ) : conflictingPaths.length > 0 ? (
              <Card className="border-warning/40 bg-warning/5 p-3">
                <div className="mb-1 flex items-center gap-2 text-[12px] text-warning">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  <strong>{conflictingPaths.length}</strong> 个文件已存在
                </div>
                <div className="max-h-24 overflow-y-auto text-[11px] text-muted-foreground">
                  {conflictingPaths.slice(0, 8).map((c) => (
                    <div key={c.relativePath} className="truncate font-mono">
                      · {c.relativePath}
                    </div>
                  ))}
                  {conflictingPaths.length > 8
                    ? `... 还有 ${conflictingPaths.length - 8} 个`
                    : null}
                </div>
              </Card>
            ) : null}

            {/* 同名策略 */}
            <div className="space-y-2">
              <div className="text-[12px] font-medium">同名文件处理：</div>
              <div className="grid grid-cols-2 gap-2">
                <label
                  className={cn(
                    'flex cursor-pointer items-start gap-2 rounded-md border p-2.5 text-[12px] transition-colors',
                    skipMode === 'skip'
                      ? 'border-primary bg-primary/5'
                      : 'border-border hover:bg-accent/40',
                  )}
                >
                  <input
                    type="radio"
                    name="skipMode"
                    checked={skipMode === 'skip'}
                    onChange={() => setSkipMode('skip')}
                    className="mt-0.5"
                  />
                  <div>
                    <div className="font-medium">跳过（推荐）</div>
                    <div className="text-[10px] text-muted-foreground">
                      保留原有，新文件不上传
                    </div>
                  </div>
                </label>
                <label
                  className={cn(
                    'flex cursor-pointer items-start gap-2 rounded-md border p-2.5 text-[12px] transition-colors',
                    skipMode === 'overwrite'
                      ? 'border-primary bg-primary/5'
                      : 'border-border hover:bg-accent/40',
                  )}
                >
                  <input
                    type="radio"
                    name="skipMode"
                    checked={skipMode === 'overwrite'}
                    onChange={() => setSkipMode('overwrite')}
                    className="mt-0.5"
                  />
                  <div>
                    <div className="font-medium">覆盖</div>
                    <div className="text-[10px] text-muted-foreground">
                      删除旧文件，重新 ingest
                    </div>
                  </div>
                </label>
              </div>
            </div>

            {/* 自动 ingest 开关 */}
            <label className="flex cursor-pointer items-center gap-2 text-[12px]">
              <input
                type="checkbox"
                checked={autoIngest}
                onChange={(e) => setAutoIngest(e.target.checked)}
              />
              <span>
                <strong>上传后自动 ingest</strong>{' '}
                <span className="text-muted-foreground">
                  （关闭则只放投喂目录，需手动点"扫描投喂文件夹"触发处理）
                </span>
              </span>
            </label>

            {/* 底部按钮 */}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" size="sm" onClick={onClose}>
                取消
              </Button>
              <Button
                size="sm"
                disabled={selectedFiles.length === 0}
                onClick={startUpload}
              >
                开始导入 {selectedFiles.length || ''}{' '}
                {selectedFiles.length ? '个文件' : ''}
              </Button>
            </div>
          </div>
        ) : null}

        {/* 分批传输中 */}
        {phase === 'uploading' ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-[13px]">
              {runner.phase === 'error' ? (
                <AlertTriangle className="h-4 w-4 text-warning" />
              ) : (
                <Loader2 className="h-4 w-4 animate-spin text-primary" />
              )}
              {runner.phase === 'error' ? (
                <span>上传中断：第 {(runner.failedAt ?? 0) + 1} 批失败</span>
              ) : (
                <span>
                  正在上传 第 {Math.min(runner.current + 1, runner.totalBatches)} /
                  {runner.totalBatches} 批 · {formatBytes(runner.uploadedBytes)} /{' '}
                  {formatBytes(runner.totalBytes)}
                </span>
              )}
            </div>
            {/* 传输进度条（按字节） */}
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{
                  width: `${
                    runner.totalBytes > 0
                      ? Math.min(100, (runner.uploadedBytes / runner.totalBytes) * 100)
                      : 0
                  }%`,
                }}
              />
            </div>
            {runner.error ? (
              <div className="rounded-md border border-warning/40 bg-warning/5 p-2 text-[11px] text-muted-foreground">
                <div className="break-all">{runner.error}</div>
              </div>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void runner.cancelUpload()}
              >
                取消上传
              </Button>
              {runner.phase === 'error' ? (
                <Button size="sm" onClick={() => void runner.retry()}>
                  重试本批
                </Button>
              ) : (
                <Button variant="outline" size="sm" onClick={onClose}>
                  <Minimize2 className="mr-1.5 h-3 w-3" />
                  转到后台
                </Button>
              )}
            </div>
            <div className="text-[11px] text-muted-foreground">
              大批量文件会自动分批传输，中途关闭弹窗不影响上传。
            </div>
          </div>
        ) : null}

        {/* 运行中 / 完成 */}
        {phase === 'running' && !taskSnapshot ? (
          <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            加载任务进度...
          </div>
        ) : null}
        {phase === 'running' && taskSnapshot ? (
          <BatchProgressView
            task={taskSnapshot}
            currentFile={currentFile}
            currentProgress={currentProgress}
            fileEvents={fileEvents}
            isCompleted={!!isCompleted}
            onResume={async () => {
              if (!taskId) return
              try {
                await api.knowledge.resumeUploadTask(taskId)
                setStreamVersion((n) => n + 1)
              } catch (e) {
                toast.error(`恢复失败：${(e as Error).message}`)
              }
            }}
            onCancel={async () => {
              if (!taskId) return
              try {
                await api.knowledge.cancelUploadTask(taskId)
                setStreamVersion((n) => n + 1)
                toast.info('已请求取消，后台停止并清理后会更新状态')
              } catch (e) {
                toast.error(`取消失败：${(e as Error).message}`)
              }
            }}
            onRetryFailed={async () => {
              if (!taskId) return
              try {
                const r = await api.knowledge.retryUploadTask(taskId)
                setStreamVersion((n) => n + 1)
                toast.info(`已重试 ${r.retried_count} 个失败文件`)
                setFileEvents((prev) => {
                  const next = { ...prev }
                  Object.keys(next).forEach((k) => {
                    if (next[+k].status === 'failed') {
                      delete next[+k]
                    }
                  })
                  return next
                })
              } catch (e) {
                toast.error(`重试失败：${(e as Error).message}`)
              }
            }}
            onDeleteFailed={async () => {
              if (!taskId) return
              try {
                const r = await api.knowledge.deleteTaskFiles(taskId, 'failed')
                toast.info(`已删除 ${r.deleted_count} 个失败文件`)
                setFileEvents((prev) => {
                  const next = { ...prev }
                  Object.keys(next).forEach((k) => {
                    if (next[+k].status === 'failed') {
                      delete next[+k]
                    }
                  })
                  return next
                })
                setTaskSnapshot((prev) =>
                  prev
                    ? {
                        ...prev,
                        failed: Math.max(0, prev.failed - r.deleted_count),
                      }
                    : prev,
                )
              } catch (e) {
                toast.error(`删除失败：${(e as Error).message}`)
              }
            }}
            onClose={() => {
              onCompleted?.()
              onClose()
            }}
            onBackground={() => {
              onCompleted?.()
              onClose()
            }}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

// ===== 进度展示子组件 =====

type BatchProgressViewProps = {
  task: UploadTask
  currentFile: string | null
  currentProgress: { percent: number | null; stage: string; detail?: string } | null
  fileEvents: Record<number, {
    status: UploadFileStatus
    relativePath: string
    skipReason?: string | null
    error?: string | null
  }>
  isCompleted: boolean
  onResume: () => Promise<void>
  onCancel: () => Promise<void>
  onRetryFailed: () => Promise<void>
  onDeleteFailed: () => Promise<void>
  onBackground: () => void
  onClose: () => void
}

// 单文件阶段 → 中文标签（与后端 _STAGE_LABEL 对应）
const STAGE_LABEL: Record<string, string> = {
  publishing: '提交新索引', cancelling: '正在取消', cleaning: '正在清理', cleanup_failed: '清理未完成',
  processing: '准备处理', resuming: '等待恢复', retrying: '等待重试',
  interrupted: '处理已中断', crashed: '处理已中断',
  parsing: '解析中',
  chunking: '切片中',
  embedding: '向量化中',
  upserting: '写入索引',
  ai_summary: 'AI 摘要',
  starting: '准备中',
}

function BatchProgressView({
  task,
  currentFile,
  currentProgress,
  fileEvents,
  isCompleted,
  onCancel,
  onResume,
  onRetryFailed,
  onDeleteFailed,
  onBackground,
  onClose,
}: BatchProgressViewProps) {
  const finished = task.done + task.skipped + task.failed
  const view = uploadProgressView(task)
  const pct = view.percent

  const failedList = Object.values(fileEvents).filter((f) => f.status === 'failed')
  const skippedList = Object.values(fileEvents).filter((f) => f.status === 'skipped')

  const [confirmingCancel, setConfirmingCancel] = useState(false)
  const [requestingCancel, setRequestingCancel] = useState(false)
  const stopping = task.status === 'cancelling' || task.status === 'cleaning'
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const handleConfirmDelete = async () => {
    setDeleting(true)
    try {
      await onDeleteFailed()
      setConfirmingDelete(false)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* 进度条 */}
      <div>
        <div className="mb-2 flex items-center justify-between text-[12px]">
          <span>
            {view.label}{task.total > 1 ? ` · ${finished} / ${task.total}` : ''}
          </span>
          <span className="text-muted-foreground">{pct == null ? '' : `${task.total === 1 && !isCompleted ? '阶段进度 ' : ''}${pct}%`}</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              'h-full rounded-full transition-all',
              pct == null && task.status === 'running' && 'animate-pulse',
              task.failed > 0
                ? 'bg-gradient-to-r from-primary to-destructive'
                : 'bg-gradient-to-r from-primary to-primary/60',
            )}
            style={{ width: pct == null ? '35%' : `${pct}%` }}
          />
        </div>
        <div className="mt-2 flex items-center gap-4 text-[11px] text-muted-foreground">
          <span>
            <CheckCircle2 className="mr-1 inline h-3 w-3 text-success" />
            完成 {task.done}
          </span>
          {task.skipped > 0 ? (
            <span className="text-warning">⤳ 跳过 {task.skipped}</span>
          ) : null}
          {task.failed > 0 ? (
            <span className="text-destructive">✗ 失败 {task.failed}</span>
          ) : null}
        </div>
      </div>

      <UploadElapsed task={task} />
      {['cancelling', 'cleaning', 'cleanup_failed', 'cancelled'].includes(task.status) && (
        <div className="rounded-md border p-3 text-xs break-words">
          {task.current_detail || '已停止导入。已完成文件和源文件保留，临时数据已清理。'}
        </div>
      )}
      <Dialog open={confirmingCancel} onOpenChange={setConfirmingCancel}>
        <DialogContent className="max-w-md">
          <DialogTitle>取消导入并清理？</DialogTitle>
          <DialogDescription>
            等待当前计算或请求结束后停止后续处理，清理本次未完成的索引和临时数据。源文件及已完成文件保留。
            {task.current_stage === 'ai_summary' ? '当前文档已入库，仅停止后续摘要处理。' : '覆盖导入在新索引提交前取消，会保留原索引。'}
          </DialogDescription>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirmingCancel(false)}>继续导入</Button>
            <Button disabled={requestingCancel} onClick={async () => {
              setRequestingCancel(true)
              try { await onCancel(); setConfirmingCancel(false) }
              finally { setRequestingCancel(false) }
            }}>确认取消并清理</Button>
          </div>
        </DialogContent>
      </Dialog>
      {/* 当前处理文件 */}
      {currentFile ? (
        <div className="rounded-md bg-muted/30 px-3 py-2 text-[12px]">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" />
            <span
              className="flex-1 truncate font-mono text-muted-foreground"
              title={currentFile}
            >
              正在处理：{currentFile}
            </span>
            {currentProgress ? (
              <span className="min-w-0 basis-full break-words text-[11px] text-muted-foreground">
                {STAGE_LABEL[currentProgress.stage] || currentProgress.stage}
                {currentProgress.detail ? ` · ${currentProgress.detail}` : ''}
                {currentProgress.percent != null ? ` · 阶段进度 ${currentProgress.percent}%` : ''}
              </span>
            ) : null}
          </div>
          {/* 当前文件内部进度细条 */}
          {currentProgress ? (
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary/60 transition-all duration-300"
                style={{ width: currentProgress.percent == null ? '35%' : `${currentProgress.percent}%` }}
              />
            </div>
          ) : null}
        </div>
      ) : null}

      {/* 跳过列表 */}
      {skippedList.length > 0 ? (
        <details className="rounded-md border bg-warning/5 p-2.5 text-[12px]">
          <summary className="cursor-pointer text-warning">
            ⤳ 跳过的文件（{skippedList.length}）
          </summary>
          <div className="mt-2 max-h-32 overflow-y-auto space-y-0.5">
            {skippedList.map((f, i) => (
              <div key={i} className="truncate font-mono text-[11px] text-muted-foreground">
                · {f.relativePath}
              </div>
            ))}
          </div>
        </details>
      ) : null}

      {/* 失败列表 + 重试 + 删除 */}
      {failedList.length > 0 ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2.5 text-[12px]">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-destructive">✗ 失败的文件（{failedList.length}）</span>
            {!confirmingDelete ? (
              <div className="flex gap-1.5">
                <Button
                  variant="outline"
                  size="sm"
                  className="h-6 px-2 text-[11px]"
                  onClick={onRetryFailed}
                  disabled={task.status === 'running' || stopping || task.status === 'cleanup_failed'}
                >
                  重试全部
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-6 px-2 text-[11px] text-destructive hover:bg-destructive/10"
                  onClick={() => setConfirmingDelete(true)}
                  disabled={deleting}
                >
                  删除全部
                </Button>
              </div>
            ) : null}
          </div>

          {confirmingDelete ? (
            <div className="mb-2 rounded border border-destructive/40 bg-background p-2 text-[11px]">
              <div className="mb-2">
                ⚠️ 将删除 <b>{failedList.length}</b> 个失败文件，包含：
                <ul className="ml-4 mt-1 list-disc space-y-0.5 text-muted-foreground">
                  <li>knowledge_files 表记录 + chunks / 摘要 / 概念引用</li>
                  <li>feed 目录下的物理文件</li>
                </ul>
                <span className="mt-1 block text-destructive">操作不可恢复。</span>
              </div>
              <div className="flex justify-end gap-1.5">
                <Button
                  variant="outline"
                  size="sm"
                  className="h-6 px-2 text-[11px]"
                  onClick={() => setConfirmingDelete(false)}
                  disabled={deleting}
                >
                  取消
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  className="h-6 px-2 text-[11px]"
                  onClick={handleConfirmDelete}
                  disabled={deleting}
                >
                  {deleting ? '删除中…' : '确认删除'}
                </Button>
              </div>
            </div>
          ) : null}

          <div className="max-h-32 overflow-y-auto space-y-1">
            {failedList.map((f, i) => (
              <div key={i} className="font-mono text-[11px]">
                <div className="truncate text-destructive">· {f.relativePath}</div>
                {f.error ? (
                  <div className="truncate text-muted-foreground" title={f.error}>
                    {f.error}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {/* 底部按钮 */}
      <div className="flex justify-end gap-2">
        {isCompleted || task.status === 'cancelled' ? (
          <Button size="sm" onClick={onClose}>
            完成
          </Button>
        ) : (
          <>
            <Button variant="ghost" size="sm" onClick={onBackground}>
              <Minimize2 className="mr-1.5 h-3 w-3" />
              转到后台
            </Button>
            {(task.status === 'paused' || task.status === 'failed') && (
              <Button size="sm" onClick={onResume}>继续导入</Button>
            )}
            <Button variant="outline" size="sm" disabled={stopping || requestingCancel}
              onClick={() => task.status === 'cleanup_failed' ? onCancel() : setConfirmingCancel(true)}>
              {task.status === 'cleanup_failed' ? '重试清理' : stopping ? '正在取消并清理' : '取消导入'}
            </Button>
          </>
        )}
      </div>
    </div>
  )
}
