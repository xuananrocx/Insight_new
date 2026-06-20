import { useEffect, useState } from 'react'
import { Loader2, Sparkles, Square } from 'lucide-react'

import type { ThinkingState } from '@/hooks/use-chat-sessions'
import { StageRow } from './stage-row'

export function ThinkingPanel({ thinking, onStop }: { thinking: ThinkingState; onStop?: () => void }) {
  const [elapsedMs, setElapsedMs] = useState(0)

  useEffect(() => {
    if (thinking.status !== 'streaming') return
    const start = thinking.startedAt
    const tick = () => setElapsedMs(Date.now() - start)
    tick()
    const id = setInterval(tick, 100)
    return () => clearInterval(id)
  }, [thinking.status, thinking.startedAt])

  const isStreaming = thinking.status === 'streaming'
  const totalSec = (elapsedMs / 1000).toFixed(1)
  const partial = thinking.partialAnswer?.trim()

  return (
    <div className="mb-3 rounded-md bg-muted/30">
      <div className="flex items-center gap-2 px-3 py-1.5 text-[11px]">
        {isStreaming ? (
          <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
        ) : (
          <Sparkles className="h-3 w-3 shrink-0 text-primary/70" />
        )}
        <span className="text-foreground/80">
          {isStreaming ? `思考中... 已用 ${totalSec}s` : `思考过程 · 已用 ${totalSec}s`}
        </span>
        <span className="flex-1" />
        {thinking.sources && thinking.sources.length > 0 ? (
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

      {thinking.warmup ? (
        <div className="border-t border-warning/30 bg-warning/10 px-3 py-1.5 text-[11px] text-warning-foreground/90">
          ⚡ {thinking.warmup}
        </div>
      ) : null}

      {thinking.stages.length > 0 ? (
        <div className="border-t border-border/30 px-3 py-2">
          {thinking.stages.map((stage, i) => (
            <StageRow key={`${stage.stage}-${i}`} stage={stage} />
          ))}
        </div>
      ) : null}

      {partial ? (
        <div className="border-t border-border/30 px-3 py-2">
          <div className="mb-1 text-[10px] text-muted-foreground">答案生成中...</div>
          <div className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-foreground/85">
            {partial}
            <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-primary/70 align-text-bottom" />
          </div>
        </div>
      ) : null}
    </div>
  )
}
