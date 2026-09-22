import { useEffect, useState } from 'react'
import { ChevronRight, Loader2, Sparkles, Square } from 'lucide-react'

import type { ThinkingState } from '@/hooks/use-chat-sessions'
import { StageRow } from './stage-row'
import { MarkdownContent } from './markdown-content'

export function ThinkingPanel({ thinking, onStop, showDetails = true, showCitations = true }: { thinking: ThinkingState; onStop?: () => void; showDetails?: boolean; showCitations?: boolean }) {
  const [elapsedMs, setElapsedMs] = useState(0)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (thinking.status !== 'streaming') return
    const start = thinking.startedAt
    const tick = () => setElapsedMs(Date.now() - start)
    tick()
    const id = setInterval(tick, 100)
    return () => clearInterval(id)
  }, [thinking.status, thinking.startedAt])

  const isStreaming = thinking.status === 'streaming'
  const duration = isStreaming ? elapsedMs : thinking.elapsedMs
  const timing = duration == null ? '' : ` · ${(duration / 1000).toFixed(1)}s`
  const partial = isStreaming ? thinking.partialAnswer?.trim() : ''
  const statusLabel = thinking.status === 'done' ? '已完成' : thinking.status === 'partial' ? '部分完成' : thinking.status === 'error' ? '未完成' : '已停止'
  const latestStage = thinking.stages.at(-1)?.label
  const label = isStreaming
    ? `${showDetails ? '思考中' : '生成中'}${showDetails && latestStage ? ` · ${latestStage}` : ''}${timing}`
    : `思考过程 · ${statusLabel}${timing}`

  return (
    <div className="mb-3 min-w-0 rounded-md bg-muted/30 [overflow-wrap:anywhere]">
      <div className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-[11px]">
        {isStreaming ? (
          <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
        ) : (
          <Sparkles className="h-3 w-3 shrink-0 text-primary/70" />
        )}
        {showDetails ? (
          <button type="button" aria-expanded={expanded} onClick={() => setExpanded(v => !v)} className="flex min-w-0 flex-1 items-center gap-1 text-left text-foreground/80">
            <ChevronRight className={`h-3 w-3 shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`} />
            <span>{label}</span>
          </button>
        ) : <span className="min-w-0 flex-1 text-foreground/80">{label}</span>}
        {showDetails && thinking.sources && thinking.sources.length > 0 ? (
          <span className="text-[10px] text-muted-foreground">📚 匹配 {thinking.sources.length} 条</span>
        ) : null}
        {isStreaming && onStop ? (
          <button
            onClick={onStop}
            className="flex shrink-0 items-center gap-1 rounded bg-destructive/10 px-2 py-0.5 text-[10px] text-destructive transition-colors hover:bg-destructive/20"
          >
            <Square className="h-2.5 w-2.5" />
            停止
          </button>
        ) : null}
      </div>

      {showDetails && expanded && thinking.warmup ? (
        <div className="border-t border-warning/30 bg-warning/10 px-3 py-1.5 text-[11px] text-warning-foreground/90">
          ⚡ {thinking.warmup}
        </div>
      ) : null}

      {showDetails && expanded && thinking.stages.length > 0 ? (
        <div className="border-t border-border/30 px-3 py-2">
          {thinking.stages.map((stage, i) => (
            <StageRow key={`${stage.stage}-${i}`} stage={stage} />
          ))}
        </div>
      ) : null}

      {partial ? (
        <div className="border-t border-border/30 px-3 py-2">
          <div className="mb-1 text-[10px] text-muted-foreground">答案生成中...</div>
          <div className="text-[12px] leading-relaxed text-foreground/85">
            <MarkdownContent content={partial} hideCitations={!showCitations} />
            <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-primary/70 align-text-bottom" />
          </div>
        </div>
      ) : null}
    </div>
  )
}
