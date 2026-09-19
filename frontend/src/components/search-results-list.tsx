import { FileText, Layers } from 'lucide-react'

import type { SearchHit } from '@/lib/api'

const STOP_TERMS = new Set([
  '怎么', '什么', '如何', '为什么', '哪些', '哪个', '哪些', '是否', '可以', '应该',
  '还是', '以及', '并且', '但是', '然后', '这个', '那个', '一个', '还有', '没有',
  'the', 'a', 'an', 'of', 'to', 'in', 'is', 'are', 'how', 'what', 'why', 'which',
  'and', 'or', 'not', 'for', 'on', 'with', 'does', 'do', 'did',
])

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function extractHighlightTerms(question: string): string[] {
  const raw = question.match(/[\w\u4e00-\u9fff]+/g) ?? []
  const seen = new Set<string>()
  const out: string[] = []
  for (const t of raw) {
    const lower = t.toLowerCase()
    if (t.length < 2 || STOP_TERMS.has(lower) || seen.has(lower)) continue
    seen.add(lower)
    out.push(t)
    if (out.length >= 8) break
  }
  return out
}

// 截取第一个命中词附近的窗口，避免长片段刷屏
function bestWindow(content: string, terms: string[]): string {
  let idx = -1
  const lower = content.toLowerCase()
  for (const t of terms) {
    const i = lower.indexOf(t.toLowerCase())
    if (i >= 0 && (idx < 0 || i < idx)) idx = i
  }
  if (idx < 0) return content.slice(0, 360)
  const start = Math.max(0, idx - 80)
  return content.slice(start, start + 400)
}

function Highlighted({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length) return <>{text}</>
  const re = new RegExp(`(${terms.map(escapeRegExp).join('|')})`, 'gi')
  const parts = text.split(re)
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded-[2px] bg-yellow-200/60 px-0.5 text-inherit dark:bg-yellow-500/30">
            {p}
          </mark>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  )
}

function scoreColor(pct: number): string {
  if (pct >= 70) return 'bg-emerald-500'
  if (pct >= 40) return 'bg-amber-500'
  return 'bg-muted-foreground/40'
}

type Props = {
  hits: SearchHit[]
  question: string
}

export function SearchResultsList({ hits, question }: Props) {
  const terms = extractHighlightTerms(question)
  if (!hits.length) {
    return <div className="text-[12px] text-muted-foreground">未检索到相关内容</div>
  }
  return (
    <div className="space-y-2">
      {hits.map((hit, idx) => (
        <div key={idx} className="rounded-lg border bg-muted/10 p-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 text-[12px] font-medium">
                <span className="text-muted-foreground">[{idx + 1}]</span>
                <span className="truncate text-accent-foreground">{hit.title || hit.source_name}</span>
                {hit.section_label ? (
                  <span className="shrink-0 rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                    {hit.section_label}
                  </span>
                ) : null}
              </div>
              <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
                <span className="inline-flex items-center gap-0.5">
                  <FileText className="h-3 w-3" />
                  {hit.source_name}
                </span>
                <span className="uppercase">{hit.file_type}</span>
                {hit.merged_chunks && hit.merged_chunks > 0 ? (
                  <span className="inline-flex items-center gap-0.5">
                    <Layers className="h-3 w-3" />
                    补全 {hit.merged_chunks} 段
                  </span>
                ) : null}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1.5" title={`相对匹配度 ${hit.score_pct}%（与本次最高结果比较，不代表正确概率）`}>
              <div className="h-1 w-14 overflow-hidden rounded-full bg-muted">
                <div className={`h-full ${scoreColor(hit.score_pct)}`} style={{ width: `${hit.score_pct}%` }} />
              </div>
              <span className="text-[10px] tabular-nums text-muted-foreground">{hit.score_pct}%</span>
            </div>
          </div>
          <div className="mt-2 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-foreground/90">
            <Highlighted text={bestWindow(hit.content, terms)} terms={terms} />
          </div>
          {hit.content.length > 400 ? (
            <details className="mt-2 text-[11px]">
              <summary className="cursor-pointer text-primary">查看完整片段</summary>
              <div className="mt-2 whitespace-pre-wrap break-words text-[12px] leading-relaxed">
                <Highlighted text={hit.content} terms={terms} />
              </div>
            </details>
          ) : null}
        </div>
      ))}
    </div>
  )
}
