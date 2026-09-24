import { ManagementDialog } from '@/components/management-dialog'
import { useConfirm } from '@/components/confirmation-provider'
import { useAuth } from '@/hooks/use-auth'
import { formatDuration } from '@/lib/format-duration'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertCircle,
  CheckCircle2,
  Copy,
  Loader2,
  Trash2,
  XCircle,
} from 'lucide-react'

import {
  DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  api,
  type AiCallLogDetail,
  type AiCallLogListItem,
  type AiCallLogStats,
} from '@/lib/api'
import { cn, formatTimestamp, formatBytes } from '@/lib/utils'

const SCENE_LABEL: Record<string, string> = {
  conversation_summary: '会话记忆 · 自动摘要',
  conversation_history: '会话记忆 · 历史查阅',
  deep_ai_tools: '深度 AI · 工具规划',
  deep_ai_answer: '深度 AI · 生成回答',
  deep_ai_verify: '深度 AI · 语义核验',
  knowledge_tool: '深度 AI · 查阅知识库',
  qa_chat: '问答',
  summarize: '摘要',
  concept_extract: '概念提取',
  title: '标题',
  test: '测试',
}

const PAGE_SIZE = 30

export function AiLogsPage() {
  const qc = useQueryClient()
  const { can } = useAuth()
  const canDetail = can('ai_logs.detail')
  const [provider, setProvider] = useState<string>('')
  const [scene, setScene] = useState<string>('')
  const [successFilter, setSuccessFilter] = useState<string>('')  // '' / 'success' / 'failed'
  const [page, setPage] = useState(0)
  const [detailId, setDetailId] = useState<number | null>(null)
  const [showDeleteAllConfirm, setShowDeleteAllConfirm] = useState(false)

  useEffect(() => {
    if (!canDetail) {
      setDetailId(null)
      void qc.cancelQueries({ queryKey: ['ai-logs', 'detail'] }).then(() => qc.removeQueries({ queryKey: ['ai-logs', 'detail'] }))
    }
  }, [canDetail, qc])

  const list = useQuery({
    queryKey: ['ai-logs', provider, scene, successFilter, page, canDetail],
    queryFn: () =>
      api.aiLogs.list({
        provider: provider || undefined,
        scene: scene || undefined,
        success: successFilter === 'success' ? true : successFilter === 'failed' ? false : undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    staleTime: 10_000,
  })

  const stats = useQuery({
    queryKey: ['ai-logs', 'stats'],
    queryFn: () => api.aiLogs.stats(),
    staleTime: 30_000,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.aiLogs.delete(id),
    onSuccess: () => {
      toast.success('已删除')
      qc.invalidateQueries({ queryKey: ['ai-logs'] })
    },
    onError: (e: Error) => toast.error(`删除失败：${e.message}`),
  })

  const cleanupMutation = useMutation({
    mutationFn: () => api.aiLogs.cleanup(),
    onSuccess: (d) => {
      toast.success(`已清理 ${d.deleted} 条过期日志`)
      qc.invalidateQueries({ queryKey: ['ai-logs'] })
    },
    onError: (e: Error) => toast.error(`清理失败：${e.message}`),
  })

  const deleteAllMutation = useMutation({
    mutationFn: () => api.aiLogs.deleteAll(),
    onSuccess: (d) => {
      toast.success(`已删除全部 ${d.deleted} 条日志`)
      setShowDeleteAllConfirm(false)
      qc.invalidateQueries({ queryKey: ['ai-logs'] })
    },
    onError: (e: Error) => toast.error(`删除失败：${e.message}`),
  })

  const items = list.data?.items ?? []
  const total = list.data?.total ?? 0

  return (
    <div className="mx-auto max-w-4xl px-8 py-8">
      <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">AI 调用日志</h1>
          <p className="mt-1 text-[12px] text-muted-foreground">
            {canDetail ? '查看自己的 AI 调用记录及完整请求、响应详情。' : '仅可查看自己的调用列表与统计，完整请求、响应和错误详情需要单独授权。'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => cleanupMutation.mutate()}
            disabled={cleanupMutation.isPending}
          >
            {cleanupMutation.isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : null}
            清理过期
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setShowDeleteAllConfirm(true)}
            disabled={total === 0 || deleteAllMutation.isPending}
          >
            <Trash2 className="mr-1.5 h-3.5 w-3.5" />
            删除全部
          </Button>
        </div>
      </div>

      {/* 统计 */}
      {stats.data ? <StatsView stats={stats.data} /> : null}

      {/* 过滤器 */}
      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-3 text-[12px]">
          <span className="text-muted-foreground">过滤：</span>
          <Select
            value={provider || 'all'}
            onValueChange={(v) => {
              setProvider(v === 'all' ? '' : v)
              setPage(0)
            }}
          >
            <SelectTrigger className="w-[130px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">所有 Provider</SelectItem>
              {stats.data?.by_provider.map((p) => (
                <SelectItem key={p.provider} value={p.provider}>
                  {p.provider} ({p.total})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={scene || 'all'}
            onValueChange={(v) => {
              setScene(v === 'all' ? '' : v)
              setPage(0)
            }}
          >
            <SelectTrigger className="w-[130px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">所有场景</SelectItem>
              {stats.data?.by_scene.map((s) => (
                <SelectItem key={s.scene} value={s.scene}>
                  {SCENE_LABEL[s.scene] || s.scene} ({s.total})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={successFilter || 'all'}
            onValueChange={(v) => {
              setSuccessFilter(v === 'all' ? '' : v)
              setPage(0)
            }}
          >
            <SelectTrigger className="w-[90px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              <SelectItem value="success">成功</SelectItem>
              <SelectItem value="failed">失败</SelectItem>
            </SelectContent>
          </Select>
          <div className="ml-auto text-muted-foreground">
            共 {total} 条 · 第 {page + 1} / {Math.max(1, Math.ceil(total / PAGE_SIZE))} 页
          </div>
        </div>
      </Card>

      {/* 列表 */}
      <Card className="p-0">
        {list.isLoading ? (
          <div className="p-8 text-center text-muted-foreground">
            <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" />
            加载中...
          </div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-[12px] text-muted-foreground">
            暂无日志记录
          </div>
        ) : (
          <div className="divide-y divide-border">
            {items.map((item) => (
              <LogRow
                key={item.id}
                item={item}
                onClick={canDetail ? () => setDetailId(item.id) : undefined}
                onDelete={() => deleteMutation.mutate(item.id)}
                deleting={deleteMutation.isPending && deleteMutation.variables === item.id}
              />
            ))}
          </div>
        )}
      </Card>

      {/* 分页 */}
      {total > PAGE_SIZE ? (
        <div className="flex items-center justify-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            上一页
          </Button>
          <span className="text-[12px] text-muted-foreground">
            {page + 1} / {Math.ceil(total / PAGE_SIZE)}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={(page + 1) * PAGE_SIZE >= total}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </Button>
        </div>
      ) : null}

      {/* 详情对话框 */}
      {canDetail && detailId !== null ? (
        <DetailDialog
          id={detailId}
          onClose={() => setDetailId(null)}
        />
      ) : null}

      {/* 删除全部二次确认 */}
      {showDeleteAllConfirm && <ManagementDialog title="删除全部 AI 调用日志" onClose={() => setShowDeleteAllConfirm(false)} busy={deleteAllMutation.isPending} className="max-w-md">
          <DialogDescription>
            即将删除全部 <span className="font-semibold text-foreground">{total}</span> 条 AI 调用日志。
          </DialogDescription>
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-[12px] leading-relaxed text-foreground">
            <div className="mb-1 font-medium text-destructive">警告：</div>
            <ul className="ml-4 list-disc space-y-1 text-muted-foreground">
              <li>操作<b className="text-foreground">不可恢复</b>，所有 LLM 调用记录将被清空。</li>
              <li>包括 system prompt / messages / response 全部内容。</li>
              <li>统计图表与按 provider/scene 聚合数据也会同步清零。</li>
              <li>如只想清理过期日志，请使用「清理过期」按钮。</li>
            </ul>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowDeleteAllConfirm(false)}
              disabled={deleteAllMutation.isPending}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => deleteAllMutation.mutate()}
              disabled={deleteAllMutation.isPending}
            >
              {deleteAllMutation.isPending ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : null}
              确认删除全部
            </Button>
          </div>
      </ManagementDialog>}
      </div>
    </div>
  )
}

// ===== 统计视图 =====

function StatsView({ stats }: { stats: AiCallLogStats }) {
  const successRate = stats.total > 0 ? ((stats.success / stats.total) * 100).toFixed(1) : '0.0'
  return (
    <div className="space-y-3">
      {/* 总览卡片 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="总调用" value={String(stats.total)} />
        <StatCard
          label="成功率"
          value={`${successRate}%`}
          sub={`成功 ${stats.success} · 失败 ${stats.failed}`}
          tone={parseFloat(successRate) >= 95 ? 'success' : 'warning'}
        />
        <StatCard
          label="平均耗时"
          value={formatDuration(stats.avg_duration_ms)}
        />
        <StatCard
          label="Provider 数"
          value={String(stats.by_provider.length)}
        />
      </div>

      {/* Provider 分布 + 趋势图 */}
      <div className="grid gap-3 lg:grid-cols-2">
        <Card className="p-3">
          <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            按 Provider
          </div>
          <div className="space-y-1.5">
            {stats.by_provider.length === 0 ? (
              <div className="text-center text-[11px] text-muted-foreground">无数据</div>
            ) : (
              stats.by_provider.map((p) => {
                const rate = p.total > 0 ? (p.success / p.total) * 100 : 0
                return (
                  <div key={p.provider} className="text-[12px]">
                    <div className="mb-0.5 flex items-center justify-between">
                      <span className="font-mono">{p.provider}</span>
                      <span className="text-muted-foreground">
                        {p.total} 次 · {rate.toFixed(0)}% 成功
                      </span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                      <div
                        className={cn(
                          'h-full rounded-full',
                          rate >= 95 ? 'bg-success' : rate >= 80 ? 'bg-warning' : 'bg-destructive',
                        )}
                        style={{ width: `${rate}%` }}
                      />
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </Card>
        <Card className="p-3">
          <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            按场景
          </div>
          <div className="space-y-1.5">
            {stats.by_scene.length === 0 ? (
              <div className="text-center text-[11px] text-muted-foreground">无数据</div>
            ) : (
              stats.by_scene.map((s) => {
                const rate = s.total > 0 ? (s.success / s.total) * 100 : 0
                return (
                  <div key={s.scene} className="text-[12px]">
                    <div className="mb-0.5 flex items-center justify-between">
                      <span>{SCENE_LABEL[s.scene] || s.scene}</span>
                      <span className="text-muted-foreground">
                        {s.total} 次 · 平均 {formatDuration(s.avg_duration_ms)}
                      </span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                      <div
                        className={cn(
                          'h-full rounded-full',
                          rate >= 95 ? 'bg-success' : rate >= 80 ? 'bg-warning' : 'bg-destructive',
                        )}
                        style={{ width: `${rate}%` }}
                      />
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </Card>
      </div>

      {/* 按天趋势 */}
      {stats.by_day.length > 0 ? (
        <Card className="p-3">
          <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            每日调用趋势
          </div>
          <DayChart data={stats.by_day} />
        </Card>
      ) : null}
    </div>
  )
}

function StatCard({
  label,
  value,
  sub,
  tone = 'default',
}: {
  label: string
  value: string
  sub?: string
  tone?: 'default' | 'success' | 'warning' | 'destructive'
}) {
  return (
    <Card className="p-3">
      <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          'mt-1 text-[22px] font-semibold tracking-tight',
          tone === 'success' && 'text-success',
          tone === 'warning' && 'text-warning',
          tone === 'destructive' && 'text-destructive',
        )}
      >
        {value}
      </div>
      {sub ? <div className="mt-0.5 text-[10px] text-muted-foreground">{sub}</div> : null}
    </Card>
  )
}

function DayChart({ data }: { data: Array<{ date: string; total: number; success: number; failed: number }> }) {
  const container = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)
  const [hovered, setHovered] = useState<string | null>(null)
  useEffect(() => {
    const element = container.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  const days = [...data].sort((a, b) => a.date.localeCompare(b.date))
  if (!days.length) return null
  const left = 44, right = width - 24, top = 16, bottom = 186
  const max = Math.max(1, ...days.map(day => Math.max(day.total, day.success, day.failed)))
  const magnitude = 10 ** Math.floor(Math.log10(max / 4))
  const step = Math.max(1, Math.ceil(max / 4 / magnitude) * magnitude)
  const ceiling = Math.ceil(max / step) * step
  const ticks = Array.from({ length: Math.round(ceiling / step) + 1 }, (_, i) => i * step)
  const timestamp = (date: string) => Date.parse(`${date}T00:00:00Z`)
  const first = timestamp(days[0].date), last = timestamp(days[days.length - 1].date)
  const x = (date: string) => first === last ? (left + right) / 2 : left + (timestamp(date) - first) / (last - first) * (right - left)
  const y = (value: number) => bottom - value / ceiling * (bottom - top)
  // Labels share the exact data-point coordinates, thinning only when space is tight.
  const tickDates = [days[0]]
  for (const day of days.slice(1, -1)) {
    if (x(day.date) - x(tickDates[tickDates.length - 1].date) >= 60 && right - x(day.date) >= 60) tickDates.push(day)
  }
  if (days.length > 1) tickDates.push(days[days.length - 1])
  const selected = days.find(day => day.date === hovered) ?? days[days.length - 1]
  const series = [
    { key: 'total' as const, label: '总调用', color: 'hsl(var(--primary))' },
    { key: 'success' as const, label: '成功', color: 'hsl(var(--success))' },
    { key: 'failed' as const, label: '失败', color: 'hsl(var(--destructive))' },
  ]
  return <div ref={container} className="min-w-0">
    <p className="text-xs text-muted-foreground">{days[0].date} 至 {days[days.length - 1].date} · 单位：次</p>
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-live="polite">
      <span className="text-muted-foreground">{selected.date}</span>
      {series.map(line => <span key={line.key} style={{ color: line.color }}>{line.label}：{selected[line.key]}</span>)}
    </div>
    <svg viewBox={`0 0 ${width} 224`} className="block h-56 w-full" role="group" aria-label="每日 AI 调用折线图，可悬停、点击或聚焦数据点查看次数">
      {ticks.map(value => <g key={value}>
        <line x1={left} y1={y(value)} x2={right} y2={y(value)} stroke="hsl(var(--border))" strokeDasharray={value ? '3 4' : undefined} />
        <text x={left - 8} y={y(value) + 4} textAnchor="end" fill="hsl(var(--muted-foreground))" fontSize="10">{value}</text>
      </g>)}
      {tickDates.map(day => <text key={day.date} x={x(day.date)} y={bottom + 22} textAnchor="middle" fill="hsl(var(--muted-foreground))" fontSize="10">{day.date.slice(5)}</text>)}
      {series.map(line => <g key={line.key}>
        <polyline points={days.map(day => `${x(day.date)},${y(day[line.key])}`).join(' ')} fill="none" stroke={line.color} strokeWidth="2" strokeLinejoin="round" strokeDasharray={line.key === 'total' ? '5 3' : undefined} />
        {days.map(day => <circle key={day.date} cx={x(day.date)} cy={y(day[line.key])} r={hovered === day.date ? 4 : 3} fill={line.color} />)}
      </g>)}
      {days.map((day, i) => <rect key={day.date}
        x={i === 0 ? left - 10 : (x(days[i - 1].date) + x(day.date)) / 2}
        width={(i === days.length - 1 ? right + 10 : (x(day.date) + x(days[i + 1].date)) / 2) - (i === 0 ? left - 10 : (x(days[i - 1].date) + x(day.date)) / 2)}
        y={top - 6} height={bottom - top + 12} fill="transparent" tabIndex={0} role="button"
        aria-label={`${day.date}，总调用 ${day.total} 次，成功 ${day.success} 次，失败 ${day.failed} 次`}
        onMouseEnter={() => setHovered(day.date)} onMouseLeave={() => setHovered(null)} onFocus={() => setHovered(day.date)} onBlur={() => setHovered(null)} onClick={() => setHovered(day.date)}
        onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setHovered(day.date) } }}
      />)}
    </svg>
    <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">{series.map(line => <span key={line.key} className="flex items-center gap-1.5"><span className="inline-block w-4 border-t-2" style={{ borderColor: line.color, borderTopStyle: line.key === 'total' ? 'dashed' : 'solid' }} />{line.label}</span>)}</div>
  </div>
}

function errorSummary(message: string) {
  try {
    const detail = JSON.parse(message)
    if (!detail?.call_id || !detail?.phase) return message
    const cause = detail.exception_chain?.[0]
    return [detail.phase, detail.http_status != null ? `HTTP ${detail.http_status}` : '未收到 HTTP 响应',
      detail.response_error || cause?.message || cause?.type, `调用 ${detail.call_id}`].filter(Boolean).join(' · ')
  } catch { return message }
}

function LogRow({
  item,
  onClick,
  onDelete,
  deleting,
}: {
  item: AiCallLogListItem
  onClick?: () => void
  onDelete: () => void
  deleting: boolean
}) {
  const confirm = useConfirm()
  const isSuccess = item.success === 1
  return (
    <div
      className={cn('flex items-center gap-3 px-3 py-2 text-[12px]', onClick && 'cursor-pointer hover:bg-accent/40')}
      title={onClick ? '查看调用详情' : '没有查看详情的权限'}
      onClick={onClick}
    >
      <div className="shrink-0">
        {isSuccess ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-success" />
        ) : (
          <XCircle className="h-3.5 w-3.5 text-destructive" />
        )}
      </div>
      <div className="w-8 shrink-0 font-mono text-[10px] text-muted-foreground" title={`日志 ID: ${item.id}`}>
        {item.id}
      </div>
      <div className="w-32 shrink-0 font-mono text-[11px]">{item.provider}</div>
      <div className="w-20 shrink-0 text-muted-foreground">
        {SCENE_LABEL[item.scene] || item.scene}
      </div>
      <div className="flex-1 truncate font-mono text-[11px] text-muted-foreground">
        {item.error_message ? (
          <span className="text-destructive">{errorSummary(item.error_message)}</span>
        ) : (
          <span>{item.response_size ? formatBytes(item.response_size) : '-'}</span>
        )}
      </div>
      <div className="w-20 shrink-0 text-right text-muted-foreground">
        {formatDuration(item.duration_ms)}
      </div>
      <div className="w-32 shrink-0 text-right text-[10px] text-muted-foreground">
        {formatTimestamp(item.created_at)}
      </div>
      <button
        className="shrink-0 text-muted-foreground hover:text-destructive"
        onClick={async (e) => {
          e.stopPropagation()
          if (await confirm('确定删除这条日志？', '删除 AI 日志')) onDelete()
        }}
        title="删除"
      >
        {deleting ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Trash2 className="h-3 w-3" />
        )}
      </button>
    </div>
  )
}

// ===== 详情对话框 =====

function DetailDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ['ai-logs', 'detail', id],
    queryFn: () => api.aiLogs.detail(id),
    staleTime: 0,
  })

  return (
    <ManagementDialog title={`AI 调用详情 #${id}`} onClose={onClose}>
        {detail.isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : detail.error ? (
          <div className="py-8 text-center text-destructive">
            加载失败：{(detail.error as Error).message}
          </div>
        ) : detail.data ? (
          <DetailContent log={detail.data} />
        ) : null}
    </ManagementDialog>
  )
}

function DetailContent({ log }: { log: AiCallLogDetail }) {
  const navigate = useNavigate()
  const isSuccess = log.success === 1
  return (
    <>
      <DialogDescription className="mb-3">
        {formatTimestamp(log.created_at)} · {log.provider} / {log.model || '?'}
      </DialogDescription>

      <div className="flex-1 space-y-3 overflow-y-auto -mx-5 px-5 pb-5">
        {/* 跳转到对应会话（仅 qa_chat 且有 session_id） */}
        {['qa_chat', 'deep_ai_tools', 'deep_ai_answer', 'deep_ai_verify', 'knowledge_tool', 'conversation_summary', 'conversation_history'].includes(log.scene) && log.session_id ? (
          <div className="flex justify-end">
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate(`/?session=${log.session_id}`)}
            >
              跳转到对应会话 →
            </Button>
          </div>
        ) : null}

        {/* 元信息 */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-md bg-muted/20 p-3 text-[11px] sm:grid-cols-3">
          <MetaItem label="场景" value={SCENE_LABEL[log.scene] || log.scene} />
          <MetaItem label="状态" value={isSuccess ? '✓ 成功' : '✗ 失败'} />
          <MetaItem label="耗时" value={formatDuration(log.duration_ms)} />
          <MetaItem label="Session" value={log.session_id || '-'} mono />
          <MetaItem label="Turn" value={log.turn_id || '-'} mono />
          <MetaItem label="KB" value={log.kb_id || '-'} mono />
          {log.token_input != null ? <MetaItem label="Input tokens" value={String(log.token_input)} /> : null}
          {log.token_output != null ? <MetaItem label="Output tokens" value={String(log.token_output)} /> : null}
        </div>

        {/* 错误信息 */}
        {!isSuccess && log.error_message ? (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3">
            <div className="mb-1 flex items-center gap-1.5 text-[12px] text-destructive">
              <AlertCircle className="h-3.5 w-3.5" />
              错误信息
            </div>
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] text-[11px] text-destructive">
              {log.error_message}
            </pre>
          </div>
        ) : null}

        {/* System Prompt */}
        {log.system_prompt ? (
          <Section title="System Prompt">
            <CopyableText text={log.system_prompt} />
          </Section>
        ) : null}

        {/* Messages */}
        {log.messages && log.messages.length > 0 ? (
          <Section title={`Messages（${log.messages.length} 条）`}>
            <div className="space-y-2">
              {log.messages.map((m, i) => (
                <div key={i} className="rounded-md border bg-muted/20 p-2">
                  <div className="mb-1 text-[10px] font-medium uppercase text-muted-foreground">
                    {m.role}
                  </div>
                  <pre className="overflow-x-auto whitespace-pre-wrap text-[11px]">
                    {typeof m.content === 'string' ? m.content : JSON.stringify(m.content, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          </Section>
        ) : null}

        {/* Response */}
        {log.response_text ? (
          <Section title="Response">
            <CopyableText text={log.response_text} />
          </Section>
        ) : null}
      </div>
    </>
  )
}

function MetaItem({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <span className="text-muted-foreground">{label}: </span>
      <span className={cn(mono && 'font-mono')}>{value}</span>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 mt-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      {children}
    </div>
  )
}

function CopyableText({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="relative rounded-md border bg-muted/20 p-2">
      <button
        className="absolute right-2 top-2 rounded bg-background/80 px-1.5 py-0.5 text-[10px] hover:bg-background"
        onClick={() => {
          navigator.clipboard.writeText(text).then(() => {
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          })
        }}
      >
        {copied ? '✓ 已复制' : (
          <span className="flex items-center gap-1">
            <Copy className="h-2.5 w-2.5" /> 复制
          </span>
        )}
      </button>
      <pre className="overflow-x-auto whitespace-pre-wrap pr-12 text-[11px] max-h-72">
        {text}
      </pre>
    </div>
  )
}
