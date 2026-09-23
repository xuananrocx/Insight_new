import { kbLabel } from '@/lib/kb-label'
// 知识库管理页
// 提供 KB 的 CRUD 操作：列出所有 KB、新建 KB、重命名 KB、删除 KB（仅非 builtin 且非默认 KB）
//                + 导出 KB Pack + 导入 KB Pack（SSE 流式进度）
import { useRef, useState } from 'react'
import { useAuth } from '@/hooks/use-auth'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import {
  api,
  type KB,
  type KbImportPrecheck,
  type KbImportProgressData,
} from '@/lib/api'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus,
  Pencil,
  Trash2,
  Database,
  FileText,
  Shield,
  Clock,
  Download,
  Upload,
  Loader2,
  AlertTriangle,
  X,
  ChevronRight,
  RefreshCw,
} from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

export default function KbPage() {
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [showCreateDialog, setShowCreateDialog] = useState(false)
  const [showEditDialog, setShowEditDialog] = useState(false)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const [selectedKb, setSelectedKb] = useState<KB | null>(null)
  const [editName, setEditName] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const [newKbName, setNewKbName] = useState('')
  const [newKbDesc, setNewKbDesc] = useState('')
  const [rebuildConfirm, setRebuildConfirm] = useState<KB | null>(null)

  // 导入相关状态
  const importFileInput = useRef<HTMLInputElement>(null)
  const importAbortRef = useRef<AbortController | null>(null)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importPrecheck, setImportPrecheck] = useState<KbImportPrecheck | null>(null)
  const [importStep, setImportStep] = useState<'idle' | 'prechecking' | 'confirm' | 'importing'>('idle')
  const [importError, setImportError] = useState<string | null>(null)
  const [importName, setImportName] = useState('')
  // 进度状态
  const [progressStage, setProgressStage] = useState<string>('')
  const [progressCurrent, setProgressCurrent] = useState(0)
  const [progressTotal, setProgressTotal] = useState(0)
  const [progressFile, setProgressFile] = useState('')

  const { data: kbs, isLoading } = useQuery({
    queryKey: ['kbs'],
    queryFn: () => api.kb.list(),
  })

  const createMutation = useMutation({
    mutationFn: () => api.kb.create({ name: newKbName, description: newKbDesc }),
    onSuccess: (data) => {
      toast.success(`已创建知识库：${data.name}`)
      setShowCreateDialog(false)
      setNewKbName('')
      setNewKbDesc('')
      queryClient.invalidateQueries({ queryKey: ['kbs'] })
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : '未知错误'
      toast.error(`创建失败：${message}`)
    },
  })

  const updateMutation = useMutation({
    mutationFn: (kb: KB) => api.kb.update(kb.id, { name: editName, description: editDesc }),
    onSuccess: (data) => {
      toast.success(`已更新知识库：${data.name}`)
      setShowEditDialog(false)
      setSelectedKb(null)
      queryClient.invalidateQueries({ queryKey: ['kbs'] })
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : '未知错误'
      toast.error(`更新失败：${message}`)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (kb: KB) => api.kb.delete(kb.id),
    onSuccess: () => {
      toast.success(`已删除知识库：${selectedKb?.name}`)
      setShowDeleteDialog(false)
      setSelectedKb(null)
      queryClient.invalidateQueries({ queryKey: ['kbs'] })
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : '未知错误'
      toast.error(`删除失败：${message}`)
    },
  })

  const rebuildMutation = useMutation({
    mutationFn: (kb: KB) => api.kb.rebuild(kb.id),
    onSuccess: (data, kb) => {
      queryClient.invalidateQueries({ queryKey: ['kbs'] })
      setRebuildConfirm(null)
      if (!data.task_id) {
        toast.info(`已清理 ${kbLabel(kb)} 的 collection（无文件需要重投喂）`)
        return
      }
      toast.success(
        `${kbLabel(kb)} 重建已启动：将重新投喂 ${data.total} 个文件（新维度 ${data.new_dim ?? '?'}维）`,
      )
      // 跳转到批量上传进度页（复用 streamUploadTask）
      navigate(`/knowledge?rebuild_task=${data.task_id}`)
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : '未知错误'
      toast.error(`重建失败：${message}`)
    },
  })

  const handleCreate = () => {
    if (!newKbName.trim()) {
      toast.error('请输入知识库名称')
      return
    }
    createMutation.mutate()
  }

  const handleUpdate = () => {
    if (!selectedKb) return
    if (!editName.trim()) {
      toast.error('请输入知识库名称')
      return
    }
    updateMutation.mutate(selectedKb)
  }

  const handleDelete = () => {
    if (!selectedKb) return
    deleteMutation.mutate(selectedKb)
  }

  const openEditDialog = (kb: KB) => {
    setSelectedKb(kb)
    setEditName(kb.name)
    setEditDesc(kb.description || '')
    setShowEditDialog(true)
  }

  const openDeleteDialog = (kb: KB) => {
    setSelectedKb(kb)
    setShowDeleteDialog(true)
  }

  // ===== 导出 =====
  const handleExport = (kb: KB) => {
    // 用 a 标签触发浏览器原生下载（避免 fetch 大文件占内存）
    const a = document.createElement('a')
    a.href = api.kb.exportUrl(kb.id)
    a.download = ''  // 让后端的 Content-Disposition 决定文件名
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    toast.success(`正在导出：${kbLabel(kb)}`)
  }

  // ===== 导入 =====
  const handleSelectImportFile = async (file: File | null) => {
    if (!file) return
    setImportFile(file)
    setImportStep('prechecking')
    setImportError(null)
    try {
      const pre = await api.kb.precheckImport(file)
      setImportPrecheck(pre)
      // 默认填 manifest 名字；重名时用 suggested_name（带后缀）
      setImportName(pre.is_name_duplicate ? pre.suggested_name : pre.kb_name)
      setImportStep('confirm')
    } catch (e) {
      const msg = e instanceof Error ? e.message : '未知错误'
      setImportError(msg)
      setImportStep('idle')
      toast.error(`预检失败：${msg}`)
    }
  }

  const importMutation = useMutation({
    mutationFn: async (vars: { file: File; newName?: string; forceRebuild: boolean }) => {
      // 走 /import 端点（只处理 precheck / needs_rebuild / ready_to_stream）
      return api.kb.importPack(vars.file, { newName: vars.newName, forceRebuild: vars.forceRebuild })
    },
    onSuccess: (data) => {
      if (!data.imported && data.needs_rebuild) {
        setImportStep('confirm')
        return
      }
      if (data.ready_to_stream) {
        // 后端就绪 → 启动 SSE 流式导入
        startStreamImport(data.compatible ? false : true)
        return
      }
      // 兜底：直接当成成功
      toast.success('已导入知识库')
      queryClient.invalidateQueries({ queryKey: ['kbs'] })
      resetImport()
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : '未知错误'
      toast.error(`导入失败：${msg}`)
      setImportError(msg)
      setImportStep('confirm')
    },
  })

  const startStreamImport = (forceRebuild: boolean) => {
    if (!importFile) return
    setImportStep('importing')
    setImportError(null)
    setProgressStage('starting')
    setProgressCurrent(0)
    setProgressTotal(0)
    setProgressFile('')

    const ac = new AbortController()
    importAbortRef.current = ac

    const trimmed = importName.trim()
    api.kb.importPackStream(
      importFile,
      { newName: trimmed || undefined, forceRebuild },
      {
        onStage: (d) => {
          setProgressTotal(d.document_count || 0)
        },
        onProgress: (d: KbImportProgressData) => {
          setProgressStage(d.stage)
          if (typeof d.current === 'number') setProgressCurrent(d.current)
          if (typeof d.total === 'number') setProgressTotal(d.total)
          if (d.file) setProgressFile(d.file)
        },
        onDone: (d) => {
          const rebuiltTxt = d.rebuilt ? '（已重建向量）' : ''
          toast.success(`已导入知识库：${d.kb_name}${rebuiltTxt}`)
          // ZIP 结构自动兼容提示
          if (d.auto_normalized) {
            setTimeout(() => {
              toast.info(
                '已自动兼容嵌套 ZIP 结构。下次打包请进入目录内选中文件再压缩。',
                { duration: 6000 },
              )
            }, 500)
          }
          queryClient.invalidateQueries({ queryKey: ['kbs'] })
          resetImport()
        },
        onError: (msg) => {
          if (ac.signal.aborted) {
            toast.info('已取消导入')
          } else {
            toast.error(`导入失败：${msg}`)
            setImportError(msg)
          }
          setImportStep('confirm')
        },
      },
      ac.signal,
    ).catch((e) => {
      if (ac.signal.aborted) {
        toast.info('已取消导入')
        resetImport()
      } else {
        const msg = e instanceof Error ? e.message : '未知错误'
        toast.error(`导入失败：${msg}`)
        setImportError(msg)
        setImportStep('confirm')
      }
    })
  }

  const handleCancelImport = () => {
    if (importAbortRef.current) {
      importAbortRef.current.abort()
      importAbortRef.current = null
    }
    // 状态恢复由 onError / catch 处理
  }

  const handleConfirmImport = (forceRebuild: boolean) => {
    if (!importFile) return
    // 第一步：调 /import 看 ready_to_stream，触发 SSE 流
    setImportStep('importing')
    setProgressStage('starting')
    const trimmed = importName.trim()
    importMutation.mutate({
      file: importFile,
      newName: trimmed || undefined,
      forceRebuild,
    })
  }

  const resetImport = () => {
    // 注意：不在这里 abort（正常完成时也会调这个，abort 会误触发"已取消"toast）
    // abort 只在 handleCancelImport 里触发
    importAbortRef.current = null
    setImportFile(null)
    setImportPrecheck(null)
    setImportStep('idle')
    setImportError(null)
    setImportName('')
    setProgressStage('')
    setProgressCurrent(0)
    setProgressTotal(0)
    setProgressFile('')
    if (importFileInput.current) importFileInput.current.value = ''
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-muted-foreground">加载中...</div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">知识库管理</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            管理多个知识库，每个知识库独立存储文档和向量
          </p>
        </div>
        <div className="flex gap-2">
          <input
            ref={importFileInput}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) handleSelectImportFile(f)
            }}
          />
          <Button
            variant="outline"
            onClick={() => importFileInput.current?.click()}
            disabled={!can('kb.create') || importStep !== 'idle'}
          >
            <Upload className="mr-2 h-4 w-4" />
            导入 Pack
          </Button>
          <Button disabled={!can('kb.create')} onClick={() => setShowCreateDialog(true)}>
            <Plus className="mr-2 h-4 w-4" />
            新建知识库
          </Button>
        </div>
      </div>

      <div className="space-y-4">
        {/* 顶部全局统计 banner */}
        {kbs && kbs.length > 0 && (
          <div className="mb-4 flex items-center gap-4 rounded-md border bg-muted/20 px-4 py-2.5 text-[12px]">
            <span className="text-muted-foreground">全局：</span>
            <span><strong>{kbs.length}</strong> 个知识库</span>
            <span className="text-muted-foreground">·</span>
            <span><strong>{kbs.reduce((a, kb) => a + kb.document_count, 0)}</strong> 个文档</span>
            <span className="text-muted-foreground">·</span>
            <span><strong>{kbs.reduce((a, kb) => a + kb.total_chunks, 0)}</strong> 个向量</span>
          </div>
        )}

        {kbs?.map((kb) => (
          <Card
            key={kb.id}
            className="group relative cursor-pointer p-5 transition-colors hover:border-primary/40"
            onClick={() => navigate(`/kbs/${kb.id}`)}
          >
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <div className="mb-3 flex items-center gap-2">
                  <Database className="h-5 w-5 text-primary" />
                  <h3 className="text-base font-semibold group-hover:text-primary">{kbLabel(kb)}</h3>
                  {kb.is_default && (
                    <span className="rounded bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                      默认
                    </span>
                  )}
                  {kb.source === 'builtin' && (
                    <span className="rounded bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                      内置
                    </span>
                  )}
                  {kb.source === 'imported' && (
                    <span className="rounded bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                      导入
                    </span>
                  )}
                  {kb.dim_mismatch && (
                    <span
                      className="flex items-center gap-1 rounded bg-destructive/10 px-2 py-0.5 text-[11px] font-medium text-destructive"
                      title={`collection 实际 ${kb.actual_collection_dim}维 ≠ 元信息声明 ${kb.embedding_dim ?? '?'}维，需重建`}
                    >
                      <AlertTriangle className="h-3 w-3" />
                      维度不匹配
                    </span>
                  )}
                  <ChevronRight className="h-4 w-4 text-muted-foreground/50 group-hover:text-primary" />
                </div>

                {kb.description && (
                  <p className="mb-3 text-sm text-muted-foreground">{kb.description}</p>
                )}

                <div className="flex gap-6 text-[13px] text-muted-foreground">
                  <div className="flex items-center gap-1.5">
                    <FileText className="h-3.5 w-3.5" />
                    <span>{kb.document_count} 个文档</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Database className="h-3.5 w-3.5" />
                    <span>{kb.total_chunks} 个向量</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Shield className="h-3.5 w-3.5" />
                    <span>
                      {kb.embedding_model ? kb.embedding_model : '使用全局默认'}
                      {kb.embedding_dim && ` (${kb.embedding_dim}维)`}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Clock className="h-3.5 w-3.5" />
                    <span>更新于 {new Date(kb.updated_at).toLocaleDateString()}</span>
                  </div>
                </div>
              </div>

              <div
                className="flex gap-2"
                onClick={(e) => e.stopPropagation()}  /* 阻止冒泡到 Card（避免点按钮触发跳转） */
              >
                {/* 导出按钮：所有 KB 都能导出（包括 default 和 builtin） */}
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!kb.capabilities.includes("export")}
                  onClick={() => handleExport(kb)}
                  title="导出 Pack"
                >
                  <Download className="h-4 w-4" />
                </Button>
                {['owner', 'manager'].includes(kb.role || '') && kb.source !== 'builtin' && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setRebuildConfirm(kb)}
                    title="重建（删 collection 重新投喂）"
                    className={kb.dim_mismatch ? 'text-destructive' : ''}
                  >
                    <RefreshCw className="h-4 w-4" />
                  </Button>
                )}
                {['owner', 'manager'].includes(kb.role || '') && kb.source !== 'builtin' && (
                  <>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => openEditDialog(kb)}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    {kb.role === 'owner' && !kb.is_default && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openDeleteDialog(kb)}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    )}
                  </>
                )}
              </div>
            </div>
          </Card>
        ))}
      </div>

      {/* 创建对话框 */}
      {showCreateDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-popover rounded-lg shadow-lg w-full max-w-md p-6">
            <h2 className="text-lg font-semibold mb-2">新建知识库</h2>
            <p className="text-sm text-muted-foreground mb-4">创建一个新的独立知识库</p>

            <div className="space-y-4 mb-6">
              <div>
                <label className="text-[13px] font-medium">名称</label>
                <input
                  type="text"
                  value={newKbName}
                  onChange={(e) => setNewKbName(e.target.value)}
                  placeholder="我的知识库"
                  className="mt-1.5 w-full rounded-md border bg-background px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="text-[13px] font-medium">描述（可选）</label>
                <textarea
                  value={newKbDesc}
                  onChange={(e) => setNewKbDesc(e.target.value)}
                  placeholder="这个知识库的用途..."
                  className="mt-1.5 w-full min-h-[80px] rounded-md border bg-background px-3 py-2 text-sm"
                />
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setShowCreateDialog(false)}>
                取消
              </Button>
              <Button onClick={handleCreate} disabled={createMutation.isPending}>
                {createMutation.isPending ? '创建中...' : '创建'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* 编辑对话框 */}
      {showEditDialog && selectedKb && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-popover rounded-lg shadow-lg w-full max-w-md p-6">
            <h2 className="text-lg font-semibold mb-2">重命名知识库</h2>
            <p className="text-sm text-muted-foreground mb-4">修改知识库名称和描述</p>

            <div className="space-y-4 mb-6">
              <div>
                <label className="text-[13px] font-medium">名称</label>
                <input
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  placeholder="知识库名称"
                  className="mt-1.5 w-full rounded-md border bg-background px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="text-[13px] font-medium">描述（可选）</label>
                <textarea
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  placeholder="这个知识库的用途..."
                  className="mt-1.5 w-full min-h-[80px] rounded-md border bg-background px-3 py-2 text-sm"
                />
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setShowEditDialog(false)}>
                取消
              </Button>
              <Button onClick={handleUpdate} disabled={updateMutation.isPending}>
                {updateMutation.isPending ? '更新中...' : '更新'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认对话框 */}
      {showDeleteDialog && selectedKb && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-popover rounded-lg shadow-lg w-full max-w-md p-6">
            <h2 className="text-lg font-semibold mb-2">删除知识库</h2>
            <p className="text-sm text-muted-foreground mb-4">
              此操作将删除知识库及其所有文档，且不可恢复
            </p>

            <div className="py-4">
              <p className="text-sm text-muted-foreground">
                确定要删除知识库 <span className="font-medium text-foreground">"{selectedKb.name}"</span> 吗？
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                • 将删除所有文档和向量数据
                <br />
                • 历史问答答案会保留（不依赖 KB）
                <br />
                • 此操作不可撤销
              </p>
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setShowDeleteDialog(false)}>
                取消
              </Button>
              <Button
                variant="destructive"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
              >
                {deleteMutation.isPending ? '删除中...' : '确认删除'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* 重建 KB 对话框 */}
      {rebuildConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-popover rounded-lg shadow-lg w-full max-w-md p-6">
            <h2 className="text-lg font-semibold mb-2 flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              重建知识库
            </h2>
            <p className="text-sm text-muted-foreground mb-4">
              此操作将按当前 embedding 模型重新生成所有向量，期间 KB 不可用。
            </p>

            <div className="py-4 space-y-2 text-sm">
              <p>
                确定要重建知识库{' '}
                <span className="font-medium text-foreground">"{rebuildConfirm.name}"</span> 吗？
              </p>
              <div className="rounded border bg-muted/50 p-3 text-xs">
                <div>📄 文档数：{rebuildConfirm.document_count}</div>
                <div>
                  📊 当前 collection：{rebuildConfirm.actual_collection_dim ?? '?'}维
                  {rebuildConfirm.dim_mismatch && (
                    <span className="text-destructive">（与元信息 {rebuildConfirm.embedding_dim ?? '?'}维 不匹配）</span>
                  )}
                </div>
                <div className="mt-2 text-muted-foreground">
                  将执行：
                  <ul className="ml-4 mt-1 list-disc space-y-0.5">
                    <li>清空 chroma collection + BM25 索引</li>
                    <li>重置所有文件状态为 pending</li>
                    <li>用当前 embedding 模型批量重新投喂</li>
                  </ul>
                </div>
                <div className="mt-2 text-destructive">
                  ⚠️ 原始文件不丢，向量数据会被替换。
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setRebuildConfirm(null)}>
                取消
              </Button>
              <Button
                variant="destructive"
                onClick={() => rebuildMutation.mutate(rebuildConfirm)}
                disabled={rebuildMutation.isPending}
              >
                {rebuildMutation.isPending ? '重建中…' : '确认重建'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* 导入 Pack 对话框 */}
      {importStep !== 'idle' && importFile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-popover rounded-lg shadow-lg w-full max-w-md p-6">
            {importStep === 'prechecking' && (
              <>
                <h2 className="text-lg font-semibold mb-2">正在预检 Pack...</h2>
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>{importFile.name}</span>
                </div>
              </>
            )}

            {importStep === 'confirm' && importPrecheck && (
              <>
                <h2 className="text-lg font-semibold mb-2">导入 Pack</h2>
                <p className="text-sm text-muted-foreground mb-3">
                  {importPrecheck.kb_description || '来自 Pack 的知识库'}
                </p>

                {/* 名称输入框 */}
                <div className="mb-4">
                  <label className="text-[13px] font-medium">新 KB 名称</label>
                  <input
                    type="text"
                    value={importName}
                    onChange={(e) => setImportName(e.target.value)}
                    placeholder="为导入的知识库命名"
                    className="mt-1.5 w-full rounded-md border bg-background px-3 py-2 text-sm"
                  />
                  {importPrecheck.is_name_duplicate && (
                    <p className="mt-1 text-[11px] text-warning">
                      ⚠ 已有同名知识库，已自动加后缀。你也可以改成其他名字。
                    </p>
                  )}
                </div>

                <div className="rounded-md border bg-muted/20 p-3 text-[12px] space-y-1 mb-4">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">文档数</span>
                    <span>{importPrecheck.document_count}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">向量数</span>
                    <span>{importPrecheck.total_chunks}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">源模型</span>
                    <span>
                      {importPrecheck.source_embedding_model ?? '未知'}
                      {importPrecheck.source_embedding_dim ? ` (${importPrecheck.source_embedding_dim}维)` : ''}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">当前模型</span>
                    <span>
                      {importPrecheck.target_embedding_model}
                      ({importPrecheck.target_embedding_dim}维)
                    </span>
                  </div>
                </div>

                {importPrecheck.compatible ? (
                  <div className="rounded-md border border-success/40 bg-success/10 p-3 text-[12px] text-success mb-4">
                    ✓ 维度兼容，可直接导入向量（秒级完成）
                  </div>
                ) : (
                  <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-[12px] mb-4">
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="h-4 w-4 text-warning shrink-0 mt-0.5" />
                      <div>
                        <div className="font-medium text-warning mb-1">维度不兼容，需要重建</div>
                        <div className="text-muted-foreground">
                          导入时将丢弃 Pack 中的向量，用当前模型重新嵌入
                          {importPrecheck.document_count} 个文档。
                          预计耗时：几分钟到十几分钟（取决于文档大小）。
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {importError && (
                  <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-[12px] text-destructive mb-3">
                    {importError}
                  </div>
                )}

                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={resetImport} disabled={importMutation.isPending}>
                    取消
                  </Button>
                  {importPrecheck.compatible ? (
                    <Button onClick={() => handleConfirmImport(false)} disabled={importMutation.isPending}>
                      {importMutation.isPending ? '导入中...' : '直接导入'}
                    </Button>
                  ) : (
                    <Button onClick={() => handleConfirmImport(true)} disabled={importMutation.isPending}>
                      {importMutation.isPending ? '重建中...' : '重建并导入'}
                    </Button>
                  )}
                </div>
              </>
            )}

            {importStep === 'importing' && (
              <>
                <h2 className="text-lg font-semibold mb-2">
                  {progressStage === 'rebuilding_embeddings'
                    ? '正在重建向量'
                    : progressStage === 'importing_vectors'
                      ? '正在导入向量'
                      : progressStage === 'extracting_documents'
                        ? '正在解压文档'
                        : '正在导入...'}
                </h2>

                {/* 进度条（仅 extracting / rebuilding 阶段有 current/total） */}
                {(progressStage === 'extracting_documents' ||
                  progressStage === 'rebuilding_embeddings') &&
                  progressTotal > 0 && (
                    <div className="mb-3">
                      <div className="mb-1 flex items-center justify-between text-[12px] text-muted-foreground">
                        <span>
                          {progressStage === 'rebuilding_embeddings'
                            ? '嵌入文档'
                            : '解压文档'}
                          ：{progressCurrent}/{progressTotal}
                        </span>
                        <span>{Math.round((progressCurrent / progressTotal) * 100)}%</span>
                      </div>
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full bg-primary transition-all"
                          style={{ width: `${(progressCurrent / progressTotal) * 100}%` }}
                        />
                      </div>
                      {progressFile && (
                        <div className="mt-1 truncate text-[11px] text-muted-foreground">
                          {progressFile}
                        </div>
                      )}
                    </div>
                  )}

                {(progressStage === 'starting' ||
                  progressStage === 'creating_kb' ||
                  progressStage === 'importing_vectors') && (
                  <div className="mb-3 flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    <span>
                      {progressStage === 'creating_kb'
                        ? '创建知识库...'
                        : progressStage === 'importing_vectors'
                          ? `导入向量（${progressCurrent}）`
                          : '准备中...'}
                    </span>
                  </div>
                )}

                <p className="mt-2 text-[12px] text-muted-foreground">
                  请勿关闭页面。完成后会自动刷新列表。
                </p>

                <div className="mt-4 flex justify-end gap-2">
                  <Button variant="outline" onClick={handleCancelImport}>
                    <X className="mr-1.5 h-4 w-4" />
                    取消
                  </Button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
