import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  Pause,
  Play,
  ScrollText,
  Trash2,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { api, type LogsInfo } from '@/lib/api'
import { cn, formatBytes, formatTimestamp } from '@/lib/utils'

const LINE_OPTIONS = [100, 200, 500, 1000]
const AUTO_REFRESH_MS = 2000

// formatBytes / formatTimestamp 用 utils 统一实现，避免 inline 重复

function formatTime(ts: number | null): string {
  // 兼容旧 API：返回 '—' 而非 '-'；ts 是秒级
  if (!ts) return '—'
  return formatTimestamp(ts)
}

function levelColor(line: string): string {
  if (line.includes(' ERROR ')) return 'text-destructive'
  if (line.includes(' WARNING ')) return 'text-warning'
  if (line.includes(' DEBUG ')) return 'text-primary'
  if (line.includes(' CRITICAL ')) return 'text-destructive font-semibold'
  return 'text-foreground/85'
}

export function LogsPage() {
  const qc = useQueryClient()
  const [lines, setLines] = useState(200)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [autoStick, setAutoStick] = useState(true)

  const infoQuery = useQuery({
    queryKey: ['logs', 'info'],
    queryFn: api.logs.info,
    refetchInterval: autoRefresh ? AUTO_REFRESH_MS : false,
  })

  const tailQuery = useQuery({
    queryKey: ['logs', 'tail', lines],
    queryFn: () => api.logs.tail(lines),
    refetchInterval: autoRefresh ? AUTO_REFRESH_MS : false,
  })

  const clearMutation = useMutation({
    mutationFn: api.logs.clear,
    onSuccess: () => {
      setShowClearConfirm(false)
      qc.invalidateQueries({ queryKey: ['logs'] })
    },
  })

  const levelQuery = useQuery({
    queryKey: ['settings', 'log_level'],
    queryFn: api.logLevel.get,
  })

  const levelMutation = useMutation({
    mutationFn: (level: string) => api.logLevel.update(level),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', 'log_level'] }),
  })

  // 自动滚到底
  useEffect(() => {
    if (autoStick && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [tailQuery.data, autoStick])

  const info: LogsInfo | undefined = infoQuery.data
  const logLines: string[] = tailQuery.data?.lines ?? []

  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-[22px] font-semibold tracking-tight">
            <ScrollText className="h-5 w-5" />
            系统日志
          </h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            应用运行日志（自动清理 7 天以上；单文件 10MB，最多 5 份）
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5 text-[12px]"
            onClick={() => qc.invalidateQueries({ queryKey: ['logs'] })}
          >
            <ScrollText className="h-3.5 w-3.5" />
            刷新
          </Button>
          <a href={api.logs.downloadUrl()} download>
            <Button variant="outline" size="sm" className="gap-1.5 text-[12px]">
              <Download className="h-3.5 w-3.5" />
              下载 zip
            </Button>
          </a>
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5 text-[12px] text-destructive hover:text-destructive"
            onClick={() => setShowClearConfirm(true)}
          >
            <Trash2 className="h-3.5 w-3.5" />
            清空
          </Button>
        </div>
      </div>

      <Card className="mb-4 p-5">
        <div className="mb-3 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-muted-foreground" />
          <span className="text-[14px] font-medium">日志级别</span>
        </div>
        <div className="flex items-center gap-2">
          {levelQuery.data?.available.map((lv) => (
            <button
              key={lv}
              onClick={() => levelMutation.mutate(lv)}
              disabled={levelMutation.isPending}
              className={cn(
                'rounded-md px-3 py-1 text-[12px] transition-colors',
                levelQuery.data?.current === lv
                  ? 'bg-primary/10 text-primary'
                  : 'bg-muted/30 text-muted-foreground hover:bg-muted/40',
              )}
            >
              {lv}
            </button>
          ))}
          <span className="ml-2 text-[11px] text-muted-foreground">
            当前：<span className="font-mono text-foreground">{levelQuery.data?.current ?? '—'}</span>
          </span>
        </div>
      </Card>

      <Card className="mb-4 p-5">
        <div className="mb-3 text-[14px] font-medium">概览</div>
        {infoQuery.isLoading ? (
          <div className="text-[12px] text-muted-foreground">加载中...</div>
        ) : info ? (
          <div className="grid grid-cols-3 gap-4 text-[12px]">
            <div>
              <div className="text-muted-foreground">总大小</div>
              <div className="font-mono text-foreground">{formatBytes(info.total_size)}</div>
            </div>
            <div>
              <div className="text-muted-foreground">文件数</div>
              <div className="font-mono text-foreground">{info.files.length}</div>
            </div>
            <div>
              <div className="text-muted-foreground">时间范围</div>
              <div className="font-mono text-[11px] text-foreground">
                {formatTime(info.oldest_mtime)} → {formatTime(info.newest_mtime)}
              </div>
            </div>
          </div>
        ) : null}
      </Card>

      <Card className="p-5">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-medium">最近日志</span>
            <div className="flex items-center gap-1">
              {LINE_OPTIONS.map((n) => (
                <button
                  key={n}
                  onClick={() => setLines(n)}
                  className={cn(
                    'rounded px-2 py-0.5 text-[11px] transition-colors',
                    lines === n
                      ? 'bg-primary/10 text-primary'
                      : 'text-muted-foreground hover:bg-accent/30',
                  )}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setAutoRefresh((v) => !v)}
              className={cn(
                'inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] transition-colors',
                autoRefresh
                  ? 'bg-success/10 text-success'
                  : 'bg-muted/30 text-muted-foreground hover:bg-muted/40',
              )}
            >
              {autoRefresh ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}
              {autoRefresh ? `自动刷新 ${AUTO_REFRESH_MS / 1000}s` : '自动刷新'}
            </button>
            <button
              onClick={() => setAutoStick((v) => !v)}
              className="inline-flex items-center gap-1 rounded-md bg-muted/30 px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted/40"
            >
              {autoStick ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              {autoStick ? '粘底' : '不粘底'}
            </button>
          </div>
        </div>

        <div
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget
            const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30
            if (atBottom !== autoStick) setAutoStick(atBottom)
          }}
          className="h-[480px] overflow-auto rounded-md bg-muted/20 p-3 font-mono text-[11px] leading-relaxed"
        >
          {tailQuery.isLoading ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              加载中...
            </div>
          ) : logLines.length === 0 ? (
            <div className="text-muted-foreground">暂无日志</div>
          ) : (
            logLines.map((line, i) => (
              <div key={i} className={cn('whitespace-pre-wrap break-all', levelColor(line))}>
                {line}
              </div>
            ))
          )}
        </div>
      </Card>

      {showClearConfirm ? (
        <ConfirmDialog
          title="清空所有日志？"
          message="将删除所有 app.log* 文件（含轮转的），仅保留一个空的 app.log。此操作不可撤销。"
          confirmText="确认清空"
          danger
          loading={clearMutation.isPending}
          onCancel={() => setShowClearConfirm(false)}
          onConfirm={() => clearMutation.mutate()}
        />
      ) : null}
    </div>
  )
}
