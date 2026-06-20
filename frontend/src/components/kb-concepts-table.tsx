import { useState } from 'react'
import { AlertCircle, Boxes, FileText, Search } from 'lucide-react'

import { api, type KbConcept } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { useQuery } from '@tanstack/react-query'

const TYPE_LABEL: Record<string, string> = {
  error_code: '错误码',
  config: '配置项',
  concept: '概念',
  component: '组件',
  command: '命令',
  metric: '指标',
  other: '其他',
}

const TYPE_COLOR: Record<string, string> = {
  error_code: 'bg-red-500/15 text-red-500',
  config: 'bg-blue-500/15 text-blue-500',
  concept: 'bg-purple-500/15 text-purple-500',
  component: 'bg-green-500/15 text-green-500',
  command: 'bg-amber-500/15 text-amber-500',
  metric: 'bg-pink-500/15 text-pink-500',
  other: 'bg-gray-500/15 text-gray-500',
}

interface Props {
  kbId: string
}

export function KbConceptsTable({ kbId }: Props) {
  const [filter, setFilter] = useState('')
  const [minMention, setMinMention] = useState(1)

  const query = useQuery({
    queryKey: ['kb-concepts', kbId, minMention],
    queryFn: () => api.kb.concepts(kbId, minMention),
    enabled: !!kbId,
  })

  const concepts = query.data?.concepts ?? []
  const filtered = filter
    ? concepts.filter(
        (c) =>
          c.name.toLowerCase().includes(filter.toLowerCase()) ||
          c.description.toLowerCase().includes(filter.toLowerCase()),
      )
    : concepts

  if (query.isLoading) {
    return (
      <Card className="mb-6 p-5">
        <div className="py-8 text-center text-[12px] text-muted-foreground">加载概念中...</div>
      </Card>
    )
  }

  return (
    <Card className="mb-6 overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-5 py-3">
        <div className="flex items-center gap-2">
          <Boxes className="h-4 w-4 text-muted-foreground" />
          <span className="text-[14px] font-medium">核心概念</span>
          <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            {concepts.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
            最小提及：
            <select
              value={minMention}
              onChange={(e) => setMinMention(Number(e.target.value))}
              className="rounded border bg-background px-1.5 py-0.5 text-[11px]"
            >
              <option value={1}>≥1</option>
              <option value={2}>≥2</option>
              <option value={3}>≥3</option>
            </select>
          </div>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="筛选..."
              className="w-32 rounded border bg-background py-1 pl-7 pr-2 text-[11px] outline-none focus:border-primary"
            />
          </div>
        </div>
      </div>

      {concepts.length === 0 ? (
        <div className="px-5 py-8 text-center text-[12px] text-muted-foreground">
          <AlertCircle className="mx-auto mb-2 h-5 w-5 opacity-40" />
          还没有 AI 概念数据。
          <div className="mt-1 text-[10px]">
            需要在 config.yaml 启用 <code className="rounded bg-muted/40 px-1 py-0.5">ingest.ai_summary.enabled: true</code> 后重新投喂文档。
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="px-5 py-8 text-center text-[12px] text-muted-foreground">
          没有匹配的概念
        </div>
      ) : (
        <div className="divide-y">
          {filtered.map((c) => (
            <ConceptRow key={c.id} concept={c} />
          ))}
        </div>
      )}
    </Card>
  )
}

function ConceptRow({ concept: c }: { concept: KbConcept }) {
  const [expanded, setExpanded] = useState(false)
  const typeClass = TYPE_COLOR[c.type] || TYPE_COLOR.other
  return (
    <button
      type="button"
      onClick={() => setExpanded(!expanded)}
      className="flex w-full items-start gap-3 px-5 py-2.5 text-left transition-colors hover:bg-accent/30"
    >
      <span className={cn('shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium', typeClass)}>
        {TYPE_LABEL[c.type] || c.type}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-medium">{c.name}</span>
          <span className="rounded bg-muted/60 px-1.5 py-0.5 text-[10px] text-muted-foreground">
            提及 {c.mention_count} 次
          </span>
          <span className="inline-flex items-center gap-0.5 rounded bg-muted/60 px-1.5 py-0.5 text-[10px] text-muted-foreground">
            <FileText className="h-2.5 w-2.5" />
            {c.file_count} 文档
          </span>
        </div>
        {c.description ? (
          <div className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            {expanded ? c.description : `${c.description.slice(0, 100)}${c.description.length > 100 ? '...' : ''}`}
          </div>
        ) : null}
        {expanded && c.file_ids.length > 0 ? (
          <div className="mt-1 text-[10px] text-muted-foreground">file_ids: {c.file_ids.join(', ')}</div>
        ) : null}
      </div>
    </button>
  )
}
