import { useState } from 'react'
import { ChevronDown, ChevronRight, Sparkles } from 'lucide-react'

import type { QaTraceStage } from '@/lib/api'
import { StageRow, formatMs } from './stage-row'

function findStage(stages: QaTraceStage[] | undefined, name: string): QaTraceStage | undefined {
  return stages?.find((s) => s.stage === name)
}

export function TracePanel({ trace }: { trace: QaTraceStage[] }) {
  const [expanded, setExpanded] = useState(false)

  if (!trace || trace.length === 0) return null

  const vecStage = findStage(trace, 'vector_retrieval')
  const bm25Stage = findStage(trace, 'bm25')
  const rerankStage = findStage(trace, 'rerank')
  const generation = findStage(trace, 'generation')
  const totalMs = trace.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0)

  const segments: string[] = []
  if (vecStage) {
    segments.push(`向量 ${vecStage.count ?? 0}${vecStage.status === 'partial' ? '↓' : ''}`)
  }
  if (bm25Stage && bm25Stage.status !== 'skipped') {
    segments.push(`BM25 ${bm25Stage.count ?? 0}`)
  }
  if (rerankStage) {
    if (rerankStage.status === 'skipped') segments.push('重排跳过')
    else if (rerankStage.status === 'failed') segments.push('重排失败')
    else segments.push(`重排选 ${rerankStage.count ?? 0}`)
  }
  if (generation?.notes?.includes('provider=')) {
    const provider = generation.notes.split('provider=')[1]?.trim()
    if (provider) segments.push(provider)
  }

  return (
    <div className="mb-3 rounded-md bg-muted/30">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-[11px] text-muted-foreground transition-colors hover:bg-accent/30"
      >
        {expanded ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
        <Sparkles className="h-3 w-3 shrink-0 text-primary/70" />
        <span className="text-foreground/80">思考过程</span>
        <span className="flex-1 truncate">
          {segments.length > 0 ? segments.join(' · ') : '点击查看详情'}
        </span>
        <span className="tabular-nums text-[10px]">{formatMs(totalMs)}</span>
      </button>

      {expanded ? (
        <div className="border-t border-border/30 px-3 py-2">
          {trace.map((stage) => (
            <StageRow key={stage.stage} stage={stage} />
          ))}
        </div>
      ) : null}
    </div>
  )
}
