import { create } from 'zustand'
import { Loader2 } from 'lucide-react'
import { api, type QaTraceStage, type SearchExpansion, type SearchHit } from '@/lib/api'
import type { ChatTurn } from '@/hooks/use-chat-sessions'
import { Button } from '@/components/ui/button'
import { SearchResultsList } from '@/components/search-results-list'
import { ThinkingPanel } from '@/components/thinking-panel'
import { useLocalStorage } from '@/hooks/use-local-storage'

type ExpansionRun = {
  controller: AbortController
  startedAt: number
  stages: QaTraceStage[]
  status: 'streaming' | 'done' | 'cancelled' | 'error'
  message?: string
}
// Survives switching conversations; a request belongs to its original turn.
const useExpansionRuns = create<{ runs: Record<string, ExpansionRun> }>(() => ({ runs: {} }))

export function SearchTurnResults({ turn, sessionId, kbScope, topK, canExpand, onSaved }: {
  turn: ChatTurn
  sessionId: string
  kbScope?: string
  topK: number
  canExpand: boolean
  onSaved: (result: SearchExpansion) => void
}) {
  const key = `${sessionId}/${turn.id}`
  const run = useExpansionRuns(s => s.runs[key])
  const [showThinking] = useLocalStorage<boolean>('amd-ui-show-thinking', true)
  const expansion = turn.expansion
  const legacy = turn.mode === 'deep'
  const running = run?.status === 'streaming'
  const expanded = !!expansion || legacy

  async function expand() {
    if (!canExpand || !kbScope || useExpansionRuns.getState().runs[key]?.status === 'streaming') return
    const controller = new AbortController()
    const startedAt = Date.now()
    function patch(update: Partial<ExpansionRun>) {
      useExpansionRuns.setState(s => ({ runs: { ...s.runs, [key]: {
        ...(s.runs[key] ?? { controller, startedAt, stages: [], status: 'streaming' }), ...update,
      } } }))
    }
    patch({ controller, startedAt, stages: [], status: 'streaming', message: undefined })
    await api.qa.askStream(turn.question, undefined, {
      onWarmup: data => patch({ message: data.msg }),
      onStage: stage => patch({ stages: [...(useExpansionRuns.getState().runs[key]?.stages ?? []), stage] }),
      onDone: data => {
        if (!data.expansion) {
          patch({ status: 'error', message: '未收到已保存的扩展结果，请刷新后重试。' })
          return
        }
        onSaved(data.expansion)
        patch({ status: 'done', message: undefined })
      },
      onError: error => patch({ status: 'error', message: `${error.message} 原结果已保留。` }),
      onCancelled: () => patch({ status: 'cancelled', message: '已取消扩展，原结果已保留。' }),
    }, controller.signal, kbScope, sessionId, turn.id, topK, 'deep', undefined, undefined, 10, undefined, 'expand')
  }

  return <div className="space-y-3">
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted-foreground">基础检索{expanded ? ' · 已扩展' : ''}</span>
      <Button type="button" size="sm" variant="outline" disabled={!canExpand || !kbScope || running}
        title="重排相关资料并补全相邻段落，仅扩展当前问题的检索结果" onClick={() => void expand()}>
        {running && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
        {running ? '正在扩展' : expanded ? '重新扩展' : '扩展检索'}
      </Button>
      {running && <Button type="button" size="sm" variant="ghost" onClick={() => run.controller.abort()}>取消扩展</Button>}
    </div>
    {running && <ThinkingPanel thinking={{ stages: run.stages, partialAnswer: '', status: 'streaming',
      startedAt: run.startedAt, warmup: run.message }} showDetails={showThinking} showCitations={false} />}
    {!running && run?.message && <p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">{run.message}</p>}
    {expansion ? <>
      <details key={expansion.completed_at} open className="rounded-md border p-3">
      <summary className="cursor-pointer text-xs text-muted-foreground">扩展结果 · {expansion.sources.length} 条 · {new Date(expansion.completed_at).toLocaleString()}</summary>
      <SearchResultsList hits={expansion.sources} question={turn.question} />
      {showThinking && expansion.trace.length > 0 && <ThinkingPanel thinking={{ stages: expansion.trace,
        partialAnswer: '', status: 'done', startedAt: 0 }} showDetails showCitations={false} />}
      </details>
      <details className="rounded-md border p-3"><summary className="cursor-pointer text-xs text-muted-foreground">
        {legacy ? '查看原有扩展结果' : '查看基础检索结果'}</summary>
        {showThinking && !!turn.trace?.length && <ThinkingPanel thinking={{ stages: turn.trace,
          partialAnswer: '', status: 'done', startedAt: 0 }} showDetails showCitations={false} />}
        <SearchResultsList hits={(turn.sources ?? []) as SearchHit[]} question={turn.question} />
      </details>
    </> : legacy ? <details open className="rounded-md border p-3">
      <summary className="cursor-pointer text-xs text-muted-foreground">扩展结果 · {turn.sources?.length ?? 0} 条</summary>
      <SearchResultsList hits={(turn.sources ?? []) as SearchHit[]} question={turn.question} />
      {showThinking && !!turn.trace?.length && <ThinkingPanel thinking={{ stages: turn.trace,
        partialAnswer: '', status: 'done', startedAt: 0 }} showDetails showCitations={false} />}
    </details> : <SearchResultsList hits={(turn.sources ?? []) as SearchHit[]} question={turn.question} />}
  </div>
}
