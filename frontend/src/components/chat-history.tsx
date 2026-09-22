import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { MessageSquare, Plus, Trash2, Search, Download, Upload, ChevronDown } from 'lucide-react'
import { toast } from 'sonner'

import { useChatSessionsCtx } from '@/hooks/chat-session-context'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAnswerStreams, abortAnswerStream } from '@/stores/answer-streams'

function relativeTime(ts: number): string {
  const diff = Date.now() - ts
  const m = Math.floor(diff / 60_000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d} 天前`
  return new Date(ts).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

function groupByDate(sessions: { id: string; updatedAt: number }[]) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const yesterday = today - 24 * 60 * 60 * 1000
  const weekAgo = today - 7 * 24 * 60 * 60 * 1000

  const groups: { label: string; items: typeof sessions }[] = [
    { label: '今天', items: [] },
    { label: '昨天', items: [] },
    { label: '本周', items: [] },
    { label: '更早', items: [] },
  ]

  for (const s of sessions) {
    if (s.updatedAt >= today) groups[0].items.push(s)
    else if (s.updatedAt >= yesterday) groups[1].items.push(s)
    else if (s.updatedAt >= weekAgo) groups[2].items.push(s)
    else groups[3].items.push(s)
  }
  return groups.filter((g) => g.items.length > 0)
}

// 浏览器侧把 Blob 保存成文件
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function ChatHistory() {
  const ctx = useChatSessionsCtx()
  const navigate = useNavigate()
  const streaming = useAnswerStreams((s) => s.streaming)
  const [query, setQuery] = useState('')
  const [exportingAll, setExportingAll] = useState(false)
  const [importing, setImporting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const filtered = query.trim()
    ? ctx.sessions.filter((s) => s.title.toLowerCase().includes(query.toLowerCase()))
    : ctx.sessions

  const groups = groupByDate(filtered.map((s) => ({ id: s.id, updatedAt: s.updated_at })))
  const sessionMap = new Map(ctx.sessions.map((s) => [s.id, s]))

  async function handleExportAll() {
    if (ctx.sessions.length === 0) {
      toast.info('暂无会话可导出')
      return
    }
    setExportingAll(true)
    try {
      const ids = ctx.sessions.map((s) => s.id)
      const { blob, filename } = await api.sessions.exportBatch(ids)
      saveBlob(blob, filename)
      toast.success(`已导出 ${ids.length} 个会话`, { description: filename })
    } catch (e) {
      toast.error('导出失败', { description: (e as Error).message })
    } finally {
      setExportingAll(false)
    }
  }

  async function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''  // 清掉，让用户能再次选同一文件
    if (!file) return
    setImporting(true)
    try {
      const result = await api.sessions.importFile(file)
      if (result.imported > 0) {
        const lines = result.sessions.map((s) => `• ${s.title}（${s.turn_count} 条）`).join('\n')
        toast.success(`已导入 ${result.imported} 个会话`, { description: lines })
      } else if (result.errors.length > 0) {
        toast.error('导入失败', { description: result.errors[0] })
      } else {
        toast.info('未导入任何会话')
      }
    } catch (e) {
      toast.error('导入失败', { description: (e as Error).message })
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between px-2 py-1">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          历史会话
        </span>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
            title="导入会话"
          >
            <Upload className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={handleExportAll}
            disabled={exportingAll || ctx.sessions.length === 0}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
            title={ctx.sessions.length === 0 ? '暂无会话' : '导出全部会话'}
          >
            <Download className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => {
              ctx.clearActive()
              navigate('/')
            }}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            title="新建会话"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept=".json,.zip,application/json,application/zip"
        onChange={handleImportFile}
        className="hidden"
      />

      {ctx.sessions.length > 5 ? (
        <div className="mb-1.5 shrink-0 px-2">
          <div className="flex items-center gap-1.5 rounded-md bg-muted/40 px-2 py-1">
            <Search className="h-3 w-3 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索会话..."
              className="w-full bg-transparent text-[11px] outline-none placeholder:text-muted-foreground"
            />
          </div>
        </div>
      ) : null}

      {ctx.sessions.length === 0 ? (
        <div className="px-3 py-2 text-[11px] text-muted-foreground">
          暂无会话，点击 + 新建
        </div>
      ) : filtered.length === 0 ? (
        <div className="px-3 py-2 text-[11px] text-muted-foreground">
          未匹配到「{query}」
        </div>
      ) : (
        <div aria-label="历史会话列表" className="min-h-0 flex-1 space-y-0.5 overflow-y-auto overscroll-contain px-1.5">
          {groups.map((group) => (
            <div key={group.label} className="mb-1">
              <div className="px-2 py-0.5 text-[9px] font-medium uppercase tracking-wider text-muted-foreground/70">
                {group.label}
              </div>
              {group.items.map((item) => {
                const s = sessionMap.get(item.id)!
                const isActive = s.id === ctx.activeId
                const turnsCount = s.turn_count
                return (
                  <HistoryItem
                    key={s.id}
                    title={s.title}
                    time={relativeTime(s.updated_at)}
                    turnsCount={turnsCount}
                    isActive={isActive}
                    streaming={!!streaming[s.id]}
                    onSelect={() => {
                      ctx.selectSession(s.id)
                      navigate('/')
                    }}
                    onDelete={() => {
                      abortAnswerStream(s.id)  // 删除正在生成的会话时先停流
                      ctx.deleteSession(s.id)
                    }}
                    onExport={async () => {
                      try {
                        const { blob, filename } = await api.sessions.exportOne(s.id)
                        saveBlob(blob, filename)
                        toast.success('已导出', { description: filename })
                      } catch (e) {
                        toast.error('导出失败', { description: (e as Error).message })
                      }
                    }}
                  />
                )
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function HistoryItem({
  title,
  time,
  turnsCount,
  isActive,
  streaming,
  onSelect,
  onDelete,
  onExport,
}: {
  title: string
  time: string
  turnsCount: number
  isActive: boolean
  streaming: boolean
  onSelect: () => void
  onDelete: () => void
  onExport: () => Promise<void>
}) {
  const [confirming, setConfirming] = useState(false)
  const [exporting, setExporting] = useState(false)
  return (
    <div
      className={cn(
        'group flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[12px] transition-colors',
        isActive
          ? 'bg-accent font-medium text-accent-foreground'
          : 'text-foreground/75 hover:bg-accent/40 hover:text-foreground',
      )}
      onClick={onSelect}
    >
      <MessageSquare className="h-3 w-3 shrink-0 opacity-60" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12px]">{title}</div>
        <div className="text-[10px] text-muted-foreground">
          {turnsCount > 0 ? `${turnsCount} 条 · ${time}` : time}
        </div>
      </div>
      {streaming ? (
        <span className="flex shrink-0 items-center gap-1 text-[10px] text-blue-500" title="后台生成中，切回可见">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-blue-500" />
          </span>
          生成中
        </span>
      ) : confirming ? (
        <div className="flex items-center gap-0.5" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => {
              onDelete()
              setConfirming(false)
            }}
            className="rounded px-1 text-[10px] text-destructive hover:bg-destructive/10"
          >
            删除
          </button>
          <button
            onClick={() => setConfirming(false)}
            className="rounded px-1 text-[10px] text-muted-foreground hover:bg-muted"
          >
            取消
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
          <button
            onClick={async (e) => {
              e.stopPropagation()
              setExporting(true)
              try {
                await onExport()
              } finally {
                setExporting(false)
              }
            }}
            disabled={exporting}
            className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
            title="导出此会话"
          >
            {exporting ? (
              <ChevronDown className="h-3 w-3 animate-pulse" />
            ) : (
              <Download className="h-3 w-3" />
            )}
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation()
              setConfirming(true)
            }}
            className="rounded p-0.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            title="删除"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      )}
    </div>
  )
}
