import { useEffect, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'

type Source = Record<string, unknown>
function text(source: Source, ...keys: string[]) {
  for (const key of keys) if (typeof source[key] === 'string' && source[key]) return source[key] as string
  return ''
}

export function CitationSources({ sources, prefix, selection }: {
  sources: unknown[]
  prefix: string
  selection: { number: number; request: number } | null
}) {
  const [expanded, setExpanded] = useState(false)
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set())
  const [highlight, setHighlight] = useState<number | null>(null)
  const groups = useMemo(() => {
    const result = new Map<string, { key: string; title: string; path: string; version: string; kb: string; entries: { number: number; section: string; snippet: string }[] }>()
    sources.forEach((value, index) => {
      const source: Source = value && typeof value === 'object' ? value as Source : {}
      const metadata = source.metadata && typeof source.metadata === 'object' ? source.metadata as Source : {}
      const path = text(source, 'source_path', 'file_path', 'rel_path') || text(metadata, 'source_path', 'file_path', 'rel_path')
      const version = text(source, 'version') || text(metadata, 'version')
      const kb = text(source, 'kb_id', 'kb_scope') || text(metadata, 'kb_id', 'kb_scope')
      const fileId = source.file_id ?? metadata.file_id
      // A title alone is not a document identity; unknown sources stay separate.
      const key = JSON.stringify([kb, fileId ?? (path || `unknown-${index}`), path, version])
      let group = result.get(key)
      if (!group) {
        group = { key, title: text(source, 'source_name', 'title') || text(metadata, 'file_name') || '未知来源', path, version, kb, entries: [] }
        result.set(key, group)
      }
      group.entries.push({ number: index + 1, section: text(source, 'section_label') || text(metadata, 'section_label'), snippet: text(source, 'content', 'text_snippet', 'text') })
    })
    return [...result.values()]
  }, [sources])

  useEffect(() => {
    if (!selection) return
    const group = groups.find(g => g.entries.some(e => e.number === selection.number))
    if (!group) return
    setExpanded(true)
    setOpenGroups(previous => new Set([...previous, group.key]))
    setHighlight(selection.number)
  }, [selection, groups])

  useEffect(() => {
    if (!selection || !expanded || highlight !== selection.number) return
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById(`${prefix}-${selection.number}`)
      target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      target?.focus({ preventScroll: true })
    })
    const timer = setTimeout(() => setHighlight(null), 2400)
    return () => { cancelAnimationFrame(frame); clearTimeout(timer) }
  }, [selection, expanded, openGroups, highlight, prefix])

  if (!sources.length) return null
  return (
    <div className="mt-3 min-w-0 [overflow-wrap:anywhere]">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(v => !v)} className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground">
        <ChevronRight className={`h-3 w-3 shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`} />
        引用来源 · {groups.length} 份文档，{sources.length} 条证据
      </button>
      {expanded && <div className="mt-2 space-y-2">
        {groups.map(group => (
          <div key={group.key} className="rounded-md border bg-muted/20 p-2 text-[11px]">
            <button type="button" aria-expanded={openGroups.has(group.key)} onClick={() => setOpenGroups(previous => {
              const next = new Set(previous)
              if (next.has(group.key)) next.delete(group.key)
              else next.add(group.key)
              return next
            })} className="flex w-full items-start gap-1 text-left font-medium text-accent-foreground">
              <ChevronRight className={`mt-0.5 h-3 w-3 shrink-0 transition-transform ${openGroups.has(group.key) ? 'rotate-90' : ''}`} />
              <span className="min-w-0">{group.title}{group.version ? ` · ${group.version}` : ''} <span className="font-normal text-muted-foreground">（{group.entries.length} 条证据）</span></span>
            </button>
            {(group.path || group.kb) && <div className="mt-1 pl-4 text-[10px] text-muted-foreground">{[group.kb, group.path].filter(Boolean).join(' · ')}</div>}
            {openGroups.has(group.key) && <div className="mt-2 space-y-2 pl-4">
              {group.entries.map(entry => (
                <div key={entry.number} id={`${prefix}-${entry.number}`} tabIndex={-1} className={`scroll-m-4 rounded p-2 transition-colors ${highlight === entry.number ? 'bg-primary/15 ring-1 ring-primary' : 'bg-background/50'}`}>
                  <div className="font-medium">[{entry.number}] {entry.section || '原文片段'}</div>
                  <div className="mt-1 whitespace-pre-wrap leading-relaxed text-muted-foreground">{entry.snippet || '此历史记录未保存原文摘录。'}</div>
                </div>
              ))}
            </div>}
          </div>
        ))}
      </div>}
    </div>
  )
}
