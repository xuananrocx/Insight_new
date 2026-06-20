import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  Library,
  RefreshCw,
  Trash2,
  FileText,
  CheckCircle2,
  Loader2,
  AlertCircle,
  HardDrive,
  Layers,
  Inbox,
  ChevronDown,
  Database,
  FileUp,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { BatchUploadDialog } from '@/components/batch-upload-dialog'
import { InterruptedTasksBanner } from '@/components/interrupted-tasks-banner'
import { api, type KnowledgeFile } from '@/lib/api'
import { cn, formatBytes as formatSize } from '@/lib/utils'

const STATUS_LABEL: Record<string, string> = {
  done: '已入库',
  pending: '处理中',
  failed: '失败',
}

const STATUS_COLOR: Record<string, string> = {
  done: 'text-success',
  pending: 'text-warning',
  failed: 'text-destructive',
}

// formatSize 用 utils.formatBytes 统一实现（看下方 import）

function fileInfo(f: KnowledgeFile) {
  return {
    name: f.relative_path.split('/').pop() ?? f.relative_path,
    dir: f.relative_path.includes('/')
      ? f.relative_path.slice(0, f.relative_path.lastIndexOf('/'))
      : '',
  }
}

export function KnowledgePage() {
  const qc = useQueryClient()
  const [scanMsg, setScanMsg] = useState<string | null>(null)
  const [selectedKbId, setSelectedKbId] = useState<string | null>(null)
  const [showKbPicker, setShowKbPicker] = useState(false)
  const [batchOpen, setBatchOpen] = useState(false)
  const [deleteFileTarget, setDeleteFileTarget] = useState<{ relPath: string; name: string } | null>(null)
  const [clearFailedOpen, setClearFailedOpen] = useState(false)
  const kbDropdownRef = useRef<HTMLDivElement>(null)
  const [searchParams] = useSearchParams()
  const kbFromUrl = searchParams.get('kb')

  const kbList = useQuery({
    queryKey: ['kbs'],
    queryFn: api.kb.list,
  })
  const defaultKb = useQuery({
    queryKey: ['settings', 'default_kb'],
    queryFn: api.defaultKb.get,
    staleTime: 60_000,
  })

  // 初始化：URL 参数 > 配置默认 > is_default 字段
  useEffect(() => {
    if (!selectedKbId && kbList.data) {
      // URL 指定的 KB 优先
      if (kbFromUrl && kbList.data.some((kb) => kb.id === kbFromUrl)) {
        setSelectedKbId(kbFromUrl)
        return
      }
      // 配置的默认 KB
      const configured = defaultKb.data?.kb_id
      if (configured && kbList.data.some((kb) => kb.id === configured)) {
        setSelectedKbId(configured)
        return
      }
      // fallback: is_default 字段
      const def = kbList.data.find((kb) => kb.is_default)
      if (def) setSelectedKbId(def.id)
    }
  }, [kbList.data, defaultKb.data, selectedKbId, kbFromUrl])

  // 点击外部关闭下拉
  useEffect(() => {
    if (!showKbPicker) return
    const onClick = (e: MouseEvent) => {
      if (kbDropdownRef.current && !kbDropdownRef.current.contains(e.target as Node)) {
        setShowKbPicker(false)
      }
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [showKbPicker])

  const stats = useQuery({
    queryKey: ['knowledge', 'stats', selectedKbId],
    queryFn: () => api.knowledge.stats(selectedKbId ?? undefined),
    refetchInterval: 5000,
  })

  const files = useQuery({
    queryKey: ['knowledge', 'files', selectedKbId],
    queryFn: () => api.knowledge.files(selectedKbId ?? undefined),
  })

  const scanMutation = useMutation({
    mutationFn: () => api.knowledge.scan(selectedKbId ?? undefined),
    onSuccess: (data) => {
      setScanMsg(data.message)
      qc.invalidateQueries({ queryKey: ['knowledge'] })
    },
    onError: (e: Error) => setScanMsg(`扫描失败：${e.message}`),
  })

  const deleteMutation = useMutation({
    mutationFn: ({ relPath, kbId }: { relPath: string; kbId?: string }) =>
      api.knowledge.remove(relPath, kbId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge'] })
    },
  })

  const clearFailedMutation = useMutation({
    mutationFn: (kbId: string) => api.kb.deleteFailedFiles(kbId),
    onSuccess: (data) => {
      toast.success(`已清理 ${data.deleted_count} 个失败文件`)
      qc.invalidateQueries({ queryKey: ['knowledge'] })
    },
    onError: (e: unknown) => {
      toast.error(`清理失败：${e instanceof Error ? e.message : '未知错误'}`)
    },
  })

  const s = stats.data
  const fileList = files.data ?? []
  const currentKb = kbList.data?.find((kb) => kb.id === selectedKbId)
  const currentKbName = currentKb?.name ?? '默认'

  return (
    <div className="mx-auto max-w-4xl px-8 py-8">
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-[22px] font-semibold tracking-tight">
            <Library className="h-5 w-5" />
            文档管理
          </h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            管理文档、查看片段、扫描投喂文件夹
          </p>
          {/* KB 选择器 */}
          <div ref={kbDropdownRef} className="relative mt-2 inline-block">
            <button
              type="button"
              onClick={() => setShowKbPicker((v) => !v)}
              className="flex items-center gap-1.5 rounded-md border border-input bg-background/40 px-2.5 py-1 text-[12px] hover:bg-accent/30 transition-colors"
            >
              <Database className="h-3 w-3 text-muted-foreground" />
              <span className="font-medium">{currentKbName}</span>
              {currentKb?.is_default ? (
                <span className="text-[10px] text-muted-foreground">默认</span>
              ) : null}
              <ChevronDown className="h-3 w-3 text-muted-foreground" />
            </button>
            {showKbPicker ? (
              <div className="absolute left-0 top-full z-50 mt-1 min-w-[240px] rounded-md border border-input bg-popover shadow-lg">
                <div className="max-h-[280px] overflow-y-auto py-1">
                  {(kbList.data ?? []).map((kb) => (
                    <button
                      key={kb.id}
                      type="button"
                      onClick={() => {
                        setSelectedKbId(kb.id)
                        setShowKbPicker(false)
                      }}
                      className={cn(
                        'flex w-full items-center justify-between px-3 py-2 text-left text-[12px] hover:bg-accent/30 transition-colors',
                        selectedKbId === kb.id && 'bg-accent/30',
                      )}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate font-medium">{kb.name}</span>
                          {kb.is_default ? (
                            <span className="text-[10px] text-muted-foreground">默认</span>
                          ) : null}
                        </div>
                        <div className="mt-0.5 text-[10px] text-muted-foreground">
                          {kb.document_count || 0} 文档 · {kb.total_chunks || 0} 片段
                        </div>
                      </div>
                      {selectedKbId === kb.id ? (
                        <CheckCircle2 className="ml-2 h-3.5 w-3.5 shrink-0 text-success" />
                      ) : null}
                    </button>
                  ))}
                  {(kbList.data ?? []).length === 0 ? (
                    <div className="px-3 py-2 text-[11px] text-muted-foreground">暂无知识库</div>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5 text-[12px]"
            onClick={() => setBatchOpen(true)}
            disabled={!selectedKbId}
            title={!selectedKbId ? '请先选择知识库' : '支持单文件/多文件/文件夹，含进度与重试'}
          >
            <FileUp className="h-3.5 w-3.5" />
            添加文档
          </Button>
          {(s?.files_failed ?? 0) > 0 ? (
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5 border-destructive/40 text-[12px] text-destructive hover:bg-destructive/10"
              onClick={() => setClearFailedOpen(true)}
              disabled={clearFailedMutation.isPending}
              title="清理当前 KB 内所有失败文件（跨 task）"
            >
              {clearFailedMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" />
              )}
              清理失败（{s?.files_failed}）
            </Button>
          ) : null}
          <Button
            size="sm"
            className="gap-1.5 text-[12px]"
            onClick={() => scanMutation.mutate()}
            disabled={scanMutation.isPending || !selectedKbId}
            title={!selectedKbId ? '请先选择知识库' : undefined}
          >
            {scanMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            扫描投喂文件夹
          </Button>
        </div>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              文档总数
            </span>
            <HardDrive className="h-3.5 w-3.5 text-muted-foreground/70" />
          </div>
          <div className="mt-2 text-[22px] font-semibold tracking-tight">
            {s?.files_total ?? '-'}
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">
            {s?.files_done ?? 0} 已入库 / {s?.files_pending ?? 0} 处理中
          </div>
        </Card>
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              知识片段
            </span>
            <Layers className="h-3.5 w-3.5 text-muted-foreground/70" />
          </div>
          <div className="mt-2 text-[22px] font-semibold tracking-tight">
            {s?.total_chunks ?? '-'}
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">向量化的语义单元</div>
        </Card>
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              待审批
            </span>
            <Inbox className="h-3.5 w-3.5 text-muted-foreground/70" />
          </div>
          <div className="mt-2 text-[22px] font-semibold tracking-tight">
            {s?.feedback_pending ?? '-'}
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">用户点赞的问答</div>
        </Card>
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              已沉淀
            </span>
            <CheckCircle2 className="h-3.5 w-3.5 text-muted-foreground/70" />
          </div>
          <div className="mt-2 text-[22px] font-semibold tracking-tight">
            {s?.feedback_approved ?? '-'}
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">入库的案例数</div>
        </Card>
      </div>

      {/* 未完成的批量上传任务 banner */}
      <div className="mb-4">
        <InterruptedTasksBanner />
      </div>

      {scanMsg ? (
        <div className="mb-4 flex items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-[12px]">
          <AlertCircle className="h-3.5 w-3.5 text-muted-foreground" />
          <span>{scanMsg}</span>
        </div>
      ) : null}

      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-medium">文档列表</span>
            <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {fileList.length}
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-[11px] text-muted-foreground"
            onClick={() => files.refetch()}
          >
            <RefreshCw className={cn('h-3 w-3', files.isFetching && 'animate-spin')} />
            刷新
          </Button>
        </div>

        {files.isLoading ? (
          <div className="flex items-center justify-center py-12 text-[12px] text-muted-foreground">
            <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
            加载中...
          </div>
        ) : fileList.length === 0 ? (
          <div className="py-12 text-center text-[12px] text-muted-foreground">
            <div className="mb-1">「{currentKbName}」为空</div>
            <div>点上方"上传文件"，或把文档放进 ~/AMD-Knowledge-Feeds/ 后点"扫描投喂文件夹"。</div>
          </div>
        ) : (
          <div className="divide-y">
            {fileList.map((f) => {
              const { name, dir } = fileInfo(f)
              return (
                <div key={f.relative_path} className="group flex items-center gap-3 px-4 py-2.5">
                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px]">{name}</div>
                    {dir ? (
                      <div className="truncate text-[10px] text-muted-foreground">{dir}/</div>
                    ) : null}
                  </div>
                  <span className="text-[11px] text-muted-foreground">
                    {f.chunk_count ? `${f.chunk_count} 片段` : ''}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    {formatSize(f.file_size)}
                  </span>
                  <span
                    className={cn(
                      'w-16 text-[11px] font-medium',
                      STATUS_COLOR[f.status] ?? 'text-muted-foreground',
                    )}
                  >
                    {STATUS_LABEL[f.status] ?? f.status}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-destructive"
                    onClick={() => {
                      const name = f.relative_path.split('/').pop() || f.relative_path
                      setDeleteFileTarget({ relPath: f.relative_path, name })
                    }}
                    disabled={deleteMutation.isPending}
                    title="删除"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              )
            })}
          </div>
        )}
      </Card>

      {/* 批量导入弹窗 */}
      {selectedKbId ? (
        <BatchUploadDialog
          open={batchOpen}
          onClose={() => setBatchOpen(false)}
          kbId={selectedKbId}
          kbName={currentKbName}
          onCompleted={() => {
            qc.invalidateQueries({ queryKey: ['knowledge'] })
            qc.invalidateQueries({ queryKey: ['upload-tasks', 'active'] })
          }}
        />
      ) : null}

      {/* 单文件删除确认 */}
      <ConfirmDialog
        open={deleteFileTarget !== null}
        onOpenChange={(o) => !o && setDeleteFileTarget(null)}
        title={`删除文件「${deleteFileTarget?.name ?? ''}」`}
        description={
          <div>
            将删除：
            <ul className="ml-4 mt-1 list-disc space-y-0.5">
              <li>knowledge_files 记录 + chunks / 摘要 / 概念引用</li>
              <li>feed 目录下的物理文件</li>
            </ul>
            <span className="mt-2 block text-destructive">操作不可恢复。</span>
          </div>
        }
        confirmText="确认删除"
        confirmVariant="destructive"
        showWarning
        loading={deleteMutation.isPending}
        onConfirm={() => {
          if (!deleteFileTarget) return
          deleteMutation.mutate(
            { relPath: deleteFileTarget.relPath, kbId: selectedKbId ?? undefined },
            { onSuccess: () => setDeleteFileTarget(null) },
          )
        }}
      />

      {/* 清理失败的二次确认 */}
      <ConfirmDialog
        open={clearFailedOpen}
        onOpenChange={setClearFailedOpen}
        title="清理失败文件"
        description={
          <div>
            将删除当前知识库内 <b>{s?.files_failed ?? 0}</b> 个失败文件，包含：
            <ul className="ml-4 mt-1 list-disc space-y-0.5">
              <li>knowledge_files 记录 + chunks / 摘要 / 概念引用</li>
              <li>feed 目录下的物理文件</li>
            </ul>
            <span className="mt-2 block text-destructive">操作不可恢复。</span>
          </div>
        }
        confirmText={`清理 ${s?.files_failed ?? 0} 个`}
        confirmVariant="destructive"
        showWarning
        loading={clearFailedMutation.isPending}
        onConfirm={() => {
          if (!selectedKbId) return
          clearFailedMutation.mutate(selectedKbId, {
            onSuccess: () => setClearFailedOpen(false),
          })
        }}
      />
    </div>
  )
}
