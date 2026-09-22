// KB 详情页：单个知识库的元信息 / 统计 / 文档预览 / 绑定的会话
import { useAuth } from '@/hooks/use-auth'
import { Grants } from '@/pages/account-page'
import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  ArrowLeft,
  Database,
  FileText,
  HardDrive,
  MessagesSquare,
  Pencil,
  Download,
  Trash2,
  Shield,
  Clock,
  ExternalLink,
} from 'lucide-react'

import { api } from '@/lib/api'
import { formatBytes as formatSize, formatRelativeTime as formatTime } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { KbConceptsTable } from '@/components/kb-concepts-table'
import { KbDocumentGraph } from '@/components/kb-document-graph'
import { KbGlobalSummaryCard } from '@/components/kb-global-summary-card'

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

// formatSize / formatTime 用 utils 统一实现，避免重复



export default function KbDetailPage() {
  const { can } = useAuth()
  const { id = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState(false)

  const kbQuery = useQuery({
    queryKey: ['kb', id],
    queryFn: () => api.kb.get(id),
    enabled: !!id,
  })

  const sessionsQuery = useQuery({
    queryKey: ['kb', id, 'sessions'],
    queryFn: () => api.kb.sessions(id),
    enabled: !!id,
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.kb.delete(id),
    onSuccess: () => {
      toast.success(`已删除知识库：${kbQuery.data?.name ?? id}`)
      qc.invalidateQueries({ queryKey: ['kbs'] })
      navigate('/kbs')
    },
    onError: (e: Error) => {
      toast.error(`删除失败：${e.message}`)
      setConfirmDelete(false)
    },
  })

  const filesQuery = useQuery({
    queryKey: ['knowledge', 'files', id],
    queryFn: () => api.knowledge.files(id),
    enabled: can("documents.view") && !!id,
  })

  const kb = kbQuery.data
  const sessions = sessionsQuery.data?.sessions ?? []
  const files = filesQuery.data ?? []

  const totalSize = useMemo(
    () => files.reduce((acc, f) => acc + (f.file_size || 0), 0),
    [files],
  )

  if (kbQuery.isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-muted-foreground">加载中...</div>
      </div>
    )
  }

  if (!kb) {
    return (
      <div className="mx-auto max-w-4xl px-8 py-8">
        <div className="text-muted-foreground">知识库不存在</div>
        <Button variant="outline" className="mt-3" onClick={() => navigate('/kbs')}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          返回列表
        </Button>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      {["owner", "manager"].includes(kb.role || "") && <Card className="mb-5 p-5"><Grants path={`/kbs/${kb.id}`} isKb /></Card>}
      {/* 顶部：返回 + 标题 + 操作 */}
      <div className="mb-6">
        <Button
          variant="ghost"
          size="sm"
          className="mb-3 gap-1 text-muted-foreground"
          onClick={() => navigate('/kbs')}
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          返回知识库列表
        </Button>
        <div className="flex items-start justify-between">
          <div className="min-w-0 flex-1">
            <h1 className="flex items-center gap-2 text-2xl font-semibold">
              <Database className="h-5 w-5 text-primary" />
              <span className="truncate">{kb.name}</span>
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
            </h1>
            {kb.description && (
              <p className="mt-2 text-sm text-muted-foreground">{kb.description}</p>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" asChild disabled={!kb.capabilities.includes("export")}>
              <a aria-disabled={!kb.capabilities.includes("export")} href={kb.capabilities.includes("export") ? api.kb.exportUrl(kb.id) : undefined}>
                <Download className="mr-1.5 h-3.5 w-3.5" />
                导出
              </a>
            </Button>
            {['owner', 'manager'].includes(kb.role || '') && kb.source !== 'builtin' && (
              <Button variant="outline" size="sm" onClick={() => navigate('/kbs')}>
                <Pencil className="mr-1.5 h-3.5 w-3.5" />
                编辑
              </Button>
            )}
            {kb.role === 'owner' && kb.source !== 'builtin' && !kb.is_default && (
              <Button
                variant="outline"
                size="sm"
                className="text-destructive"
                disabled={deleteMutation.isPending}
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                {deleteMutation.isPending ? '删除中...' : '删除'}
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* 统计卡片 */}
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              文档数
            </span>
            <FileText className="h-3.5 w-3.5 text-muted-foreground/70" />
          </div>
          <div className="mt-2 text-[22px] font-semibold tracking-tight">
            {kb.document_count}
          </div>
        </Card>
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              向量数
            </span>
            <Database className="h-3.5 w-3.5 text-muted-foreground/70" />
          </div>
          <div className="mt-2 text-[22px] font-semibold tracking-tight">
            {kb.total_chunks}
          </div>
        </Card>
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              磁盘占用
            </span>
            <HardDrive className="h-3.5 w-3.5 text-muted-foreground/70" />
          </div>
          <div className="mt-2 text-[22px] font-semibold tracking-tight">
            {formatSize(totalSize)}
          </div>
        </Card>
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              绑定会话
            </span>
            <MessagesSquare className="h-3.5 w-3.5 text-muted-foreground/70" />
          </div>
          <div className="mt-2 text-[22px] font-semibold tracking-tight">
            {sessions.length}
          </div>
        </Card>
      </div>

      {/* 元信息 */}
      <Card className="mb-6 p-5">
        <div className="mb-3 flex items-center gap-2">
          <Shield className="h-4 w-4 text-muted-foreground" />
          <span className="text-[14px] font-medium">元信息</span>
        </div>
        <div className="grid grid-cols-1 gap-x-8 gap-y-2 text-[12px] sm:grid-cols-2">
          <div className="flex justify-between border-b border-border/40 py-1.5">
            <span className="text-muted-foreground">KB ID</span>
            <code className="font-mono text-[11px]">{kb.id}</code>
          </div>
          <div className="flex justify-between border-b border-border/40 py-1.5">
            <span className="text-muted-foreground">Collection</span>
            <code className="font-mono text-[11px] truncate max-w-[300px]" title={kb.collection_name}>
              {kb.collection_name}
            </code>
          </div>
          <div className="flex justify-between border-b border-border/40 py-1.5">
            <span className="text-muted-foreground">来源</span>
            <span>{kb.source}</span>
          </div>
          <div className="flex justify-between border-b border-border/40 py-1.5">
            <span className="text-muted-foreground">Embedding 模型</span>
            <span>
              {kb.embedding_model ?? '使用全局默认'}
              {kb.embedding_dim && ` (${kb.embedding_dim}维)`}
            </span>
          </div>
          <div className="flex justify-between border-b border-border/40 py-1.5">
            <span className="text-muted-foreground">创建时间</span>
            <span>{new Date(kb.created_at).toLocaleString()}</span>
          </div>
          <div className="flex justify-between border-b border-border/40 py-1.5">
            <span className="text-muted-foreground">更新时间</span>
            <span>{new Date(kb.updated_at).toLocaleString()}</span>
          </div>
        </div>
      </Card>

      {/* 文档列表（只读） */}
      <Card className="mb-6 overflow-hidden">
        <div className="flex items-center justify-between border-b px-5 py-3">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-muted-foreground" />
            <span className="text-[14px] font-medium">文档</span>
            <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {files.length}
            </span>
          </div>
          <Button variant="outline" size="sm" asChild>
            <Link to={`/knowledge?kb=${kb.id}`}>
              管理文档
              <ExternalLink className="ml-1.5 h-3 w-3" />
            </Link>
          </Button>
        </div>
        {filesQuery.isLoading ? (
          <div className="py-8 text-center text-[12px] text-muted-foreground">
            加载中...
          </div>
        ) : files.length === 0 ? (
          <div className="py-8 text-center text-[12px] text-muted-foreground">
            这个知识库还没有文档
          </div>
        ) : (
          <div className="divide-y">
            {files.map((f) => {
              const name = f.relative_path.split('/').pop() ?? f.relative_path
              const dir = f.relative_path.includes('/')
                ? f.relative_path.slice(0, f.relative_path.lastIndexOf('/'))
                : ''
              return (
                <div key={f.relative_path} className="flex items-center gap-3 px-5 py-2.5">
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
                    className={`w-16 text-[11px] font-medium ${
                      STATUS_COLOR[f.status] ?? 'text-muted-foreground'
                    }`}
                  >
                    {STATUS_LABEL[f.status] ?? f.status}
                  </span>
                  {kb.capabilities.includes('download') && <Button size="sm" variant="ghost" asChild><a href={`/api/v1/knowledge/download/${f.id}?kb_id=${encodeURIComponent(kb.id)}`}>下载</a></Button>}
                </div>
              )
            })}
          </div>
        )}
      </Card>

      {/* KB 全局概览（迭代 6）*/}
      <KbGlobalSummaryCard kbId={kb.id} canManage={kb.capabilities.includes('manage')} />

      {/* 核心概念（迭代 4）*/}
      <KbConceptsTable kbId={kb.id} />

      {/* 文档关联图（迭代 4）*/}
      <KbDocumentGraph kbId={kb.id} />

      {/* 绑定的会话 */}
      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b px-5 py-3">
          <div className="flex items-center gap-2">
            <MessagesSquare className="h-4 w-4 text-muted-foreground" />
            <span className="text-[14px] font-medium">绑定的会话</span>
            <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {sessions.length}
            </span>
          </div>
        </div>
        {sessionsQuery.isLoading ? (
          <div className="py-8 text-center text-[12px] text-muted-foreground">
            加载中...
          </div>
        ) : sessions.length === 0 ? (
          <div className="py-8 text-center text-[12px] text-muted-foreground">
            还没有用这个知识库的会话
          </div>
        ) : (
          <div className="divide-y">
            {sessions.map((s) => (
              <Link
                key={s.id}
                to={`/?session=${s.id}`}
                className="group flex items-center gap-3 px-5 py-2.5 hover:bg-accent/30 transition-colors"
              >
                <MessagesSquare className="h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] group-hover:text-primary">
                    {s.title || '新会话'}
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                    <Clock className="h-2.5 w-2.5" />
                    {formatTime(s.updated_at)}
                  </div>
                </div>
                <span className="text-[11px] text-muted-foreground">
                  {s.turn_count} 轮对话
                </span>
              </Link>
            ))}
          </div>
        )}
      </Card>

      {/* 删除二次确认 */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <Card className="max-w-md p-6">
            <h3 className="mb-3 text-lg font-semibold">删除知识库</h3>
            <p className="mb-2 text-sm text-muted-foreground">
              确认删除知识库「{kbQuery.data?.name}」？此操作不可撤销，将一并删除：
            </p>
            <ul className="mb-4 ml-5 list-disc text-sm text-muted-foreground">
              <li>所有文档记录与向量索引</li>
              <li>文档级 AI 摘要、概念、关联图</li>
              <li>KB 全局摘要</li>
              <li>投喂文件夹中该 KB 子目录的所有文件</li>
            </ul>
            {(sessionsQuery.data?.sessions?.length ?? 0) > 0 && (
              <p className="mb-4 rounded bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
                绑定此 KB 的 {sessionsQuery.data?.sessions?.length} 个会话将解绑（保留会话历史）
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setConfirmDelete(false)} disabled={deleteMutation.isPending}>
                取消
              </Button>
              <Button
                variant="destructive"
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
              >
                {deleteMutation.isPending ? '删除中...' : '确认删除'}
              </Button>
            </div>
          </Card>
        </div>
      )}
    </div>
  )
}
