import { useState } from 'react'

import type { QaTraceCandidate, QaTraceStage } from '@/lib/api'
import { cn } from '@/lib/utils'

export const STATUS_STYLE: Record<string, string> = {
  ok: 'bg-success/80',
  partial: 'bg-warning/80',
  skipped: 'bg-muted-foreground/40',
  failed: 'bg-destructive/80',
  empty: 'bg-muted-foreground/40',
}

export const SCORE_TYPE_STYLE: Record<string, string> = {
  cosine: 'bg-primary/15 text-primary',
  bm25: 'bg-purple-500/15 text-purple-400',
  rrf: 'bg-cyan-500/15 text-cyan-400',
  rerank: 'bg-orange-500/15 text-orange-400',
}

export function formatMs(ms?: number): string {
  if (!ms || ms <= 0) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function StageRow({ stage }: { stage: QaTraceStage }) {
  const [showCandidates, setShowCandidates] = useState(false)
  const candidates = stage.candidates ?? []
  const hasCandidates = candidates.length > 0
  const status = stage.status ?? 'ok'

  return (
    <div className="mb-2 min-w-0 [overflow-wrap:anywhere] last:mb-0">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
        <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', STATUS_STYLE[status] ?? STATUS_STYLE.ok)} />
        <span className="min-w-0 font-medium text-foreground/90">{stage.label}</span>
        <span className="shrink-0 rounded bg-muted/60 px-1.5 py-0.5 text-[10px] tabular-nums text-muted-foreground">
          {stage.count ?? 0} 候选
        </span>
        {stage.duration_ms ? (
          <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/70">{formatMs(stage.duration_ms)}</span>
        ) : null}
        {hasCandidates ? (
          <button
            onClick={() => setShowCandidates((v) => !v)}
            className="ml-auto shrink-0 text-[10px] text-muted-foreground transition-colors hover:text-foreground"
          >
            {showCandidates ? '隐藏候选' : `查看 ${candidates.length} 条`}
          </button>
        ) : null}
      </div>

      {stage.notes ? (
        <div className="mt-1 whitespace-pre-wrap pl-3.5 text-[10px] leading-relaxed text-muted-foreground/70">
          {stage.notes}
        </div>
      ) : null}

      {showCandidates && hasCandidates ? (
        <div className="mt-1 space-y-0.5 border-l border-border/40 pl-3">
          {candidates.map((c, i) => (
            <CandidateRow key={i} candidate={c} rank={i + 1} />
          ))}
        </div>
      ) : null}
    </div>
  )
}

export function CandidateRow({ candidate, rank }: { candidate: QaTraceCandidate; rank: number }) {
  const name = candidate.source_name || candidate.title || '未命名'
  const scoreType = candidate.score_type ?? ''
  return (
    <div className="flex items-start gap-1.5 py-0.5 text-[10px]">
      <span className="mt-0.5 shrink-0 tabular-nums text-muted-foreground/50">{rank}.</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-foreground/85">{name}</span>
          {typeof candidate.score === 'number' ? (
            <span className="shrink-0 tabular-nums text-muted-foreground">{candidate.score.toFixed(3)}</span>
          ) : null}
          {scoreType ? (
            <span className={cn('shrink-0 rounded px-1 py-0.5 text-[9px] font-medium', SCORE_TYPE_STYLE[scoreType] ?? 'bg-muted text-muted-foreground')}>
              {scoreType}
            </span>
          ) : null}
        </div>
        {candidate.preview ? (
          <div className="mt-0.5 line-clamp-1 text-muted-foreground/70">{candidate.preview}</div>
        ) : null}
      </div>
    </div>
  )
}
