import { elapsedAnswer, snapshotTiming } from '@/lib/answer-timing'
import { apiFetch } from '@/lib/account-api'
import { answerJobPath } from '@/lib/answer-jobs'
import { ChatSessionHeader } from '@/components/chat-session-header'
import { ApiModelSelect } from '@/components/api-model-select'
import { accountApi, type ModelSelection } from '@/lib/account-api'
import { ManagementDialog } from '@/components/management-dialog'
import { createUuid } from '@/lib/uuid'
import { MessageNavigation } from '@/components/message-navigation'
import { useChatScroll } from '@/hooks/use-chat-scroll'
import { kbLabel, kbMetaSuffix } from '@/lib/kb-label'
import { useDeepAiOptions } from '@/hooks/use-deep-ai-options'
import { useEffect, useId, useState, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  ArrowUp,
  ArrowDown,
  Library,
  Loader2,
  AlertCircle,
  AlertTriangle,
  BarChart3,
  Info,
  Copy,
  Check,
} from 'lucide-react'

import { AutosizeTextarea } from '@/components/autosize-textarea'
import { mergeStage, stripRunningStages } from '@/lib/trace-stages'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { StatsCards } from '@/components/stats-cards'
import { MarkdownContent } from '@/components/markdown-content'
import { ThinkingPanel } from '@/components/thinking-panel'
import { CitationSources } from '@/components/citation-sources'
import { TopKSelect } from '@/components/top-k-select'
import { RetrievalModeSelect } from '@/components/retrieval-mode-select'
import { SearchTurnResults } from '@/components/search-turn-results'
import { useChatSessionsCtx } from '@/hooks/chat-session-context'
import { useLocalStorage } from '@/hooks/use-local-storage'
import { useApiRetryCount } from '@/hooks/use-api-retry-count'
import { useAuth } from '@/hooks/use-auth'
import { type ChatTurn, type ThinkingState } from '@/hooks/use-chat-sessions'
import { api, type ChatMessage, type RetrievalMode, type QaSource, type QaTraceStage, type KB } from '@/lib/api'
import { useAnswerStreams, abortAnswerStream } from '@/stores/answer-streams'

// thinking 重建守卫：流式期间若 turnsState / detail cache 被 refetch 重置导致 thinking 丢失，
// 从回调现场重建，避免后续 token 被 `t.thinking ?` 守卫静默丢弃
function patchThinking(t: ChatTurn, patch: (th: ThinkingState) => ThinkingState): ChatTurn {
  const base: ThinkingState =
    t.thinking ?? { stages: [], partialAnswer: '', status: 'streaming', startedAt: Date.now() }
  return { ...t, thinking: patch(base) }
}

// 知识库选择器：warn=新提问界面（警示琥珀胶囊，提醒发问前核对），calm=会话中（纯文本，同原样式）
function KbPicker({
  kbs,
  value,
  label,
  onPick,
  variant,
  disabled,
  title,
}: {
  kbs: KB[] | undefined
  value: string | null
  label: string
  onPick: (kbId: string) => void
  variant: 'warn' | 'calm'
  disabled?: boolean
  title?: string
}) {
  const triggerCls =
    variant === 'warn'
      ? 'rounded-full border-warning/50 bg-warning/15 px-2.5 font-semibold text-warning-foreground hover:border-warning/80 focus-visible:ring-warning/50 [&_svg]:text-warning-foreground/70'
      : 'rounded border-0 bg-transparent px-0.5 font-medium text-accent-foreground hover:bg-accent/30 [&_svg]:text-muted-foreground'
  const currentKb = kbs?.find(kb => kb.id === value)
  return (
    <Select value={value ?? undefined} onValueChange={onPick} disabled={disabled}>
      <SelectTrigger
        aria-label='选择知识库'
        title={title}
        className={`w-auto max-w-[min(48vw,20rem)] gap-1 py-0.5 text-[11px] ${triggerCls}`}
      >
        <SelectValue placeholder='请选择知识库'>
          {currentKb ? (
            <>
              {currentKb.name}
              <span className='opacity-60'>{kbMetaSuffix(currentKb)}</span>
            </>
          ) : (
            label
          )}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {kbs?.map(kb => (
          <SelectItem key={kb.id} value={kb.id}>
            <div className='flex flex-col gap-0.5 py-0.5'>
              <span>
                {kb.name}
                <span className='opacity-60'>{kbMetaSuffix(kb)}</span>
                {kb.is_default && <span className='ml-1 text-[10px] text-muted-foreground'>（默认）</span>}
              </span>
              <span className='text-[10px] text-muted-foreground'>{kb.document_count || 0} 个文档</span>
            </div>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function NewChatGreeting() {
  const { user } = useAuth()
  const [hour, setHour] = useState(() => new Date().getHours())

  useEffect(() => {
    const update = () => setHour(new Date().getHours())
    const timer = window.setInterval(update, 1000)
    window.addEventListener('focus', update)
    document.addEventListener('visibilitychange', update)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', update)
      document.removeEventListener('visibilitychange', update)
    }
  }, [])

  const greeting = hour >= 5 && hour < 11 ? '上午好'
    : hour >= 11 && hour < 14 ? '中午好'
    : hour >= 14 && hour < 18 ? '下午好' : '晚上好'
  const name = user?.display_name?.trim() || user?.username?.trim()

  return (
    <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
      <h1 className="break-words text-[22px] font-semibold tracking-tight">
        {greeting}{name ? `，${name}` : ''}
      </h1>
      <p className="text-[13px] text-muted-foreground">今天想了解些什么？</p>
    </div>
  )
}

export function ChatPage() {
  const ctx = useChatSessionsCtx()
  const session = ctx.activeSession
  const [input, setInput] = useState('')
  const [showSessionInfo, setShowSessionInfo] = useState(false)
  // 空状态（无会话）下选的检索模式，首问建会话时带过去
  const [legacyModeNotice, setLegacyModeNotice] = useLocalStorage('amd-ui-mode-merge-notice', false)
  useEffect(() => {
    if (session?.retrieval_mode === 'deep' && !legacyModeNotice) {
      toast.info('深度检索已合并到基础检索，可在每轮结果中点击扩展检索。')
      setLegacyModeNotice(true)
    }
  }, [session?.retrieval_mode, legacyModeNotice, setLegacyModeNotice])
  const [newSessionMode, setNewSessionMode] = useState<RetrievalMode>('deep_ai')
  const [modeCounts, setModeCounts] = useLocalStorage<Record<string, number>>('amd-search-counts-by-mode', { basic: 10, ai: 10, deep_ai: 10 })
  const currentMode = session?.retrieval_mode ?? newSessionMode
  const countMode = currentMode === 'deep' ? 'basic' : currentMode
  const countFor = (mode: RetrievalMode) => {
    const stored = modeCounts?.[mode === 'deep' ? 'basic' : mode]
    return [5, 10, 15, 20].includes(stored) ? stored : 10
  }
  const topK = countFor(countMode)
  const setTopK = (value: number) => setModeCounts(previous => ({ ...previous, [countMode]: value }))

  const [showStats, setShowStats] = useLocalStorage('amd-ui-show-stats', true)
  const [searchParams] = useSearchParams()

  // 从 URL ?session=xxx 自动选中会话（KB 详情页等场景跳转用）
  useEffect(() => {
    const sid = searchParams.get('session')
    if (sid && sid !== ctx.activeId) {
      // 确认这个会话存在再选
      if (ctx.sessions.some((s) => s.id === sid)) {
        ctx.selectSession(sid)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])
  const [pendingKbScope, setPendingKbScope] = useState<string | null>(null)
  const [switchingKb, setSwitchingKb] = useState(false)

  const modelQc = useQueryClient()
  const { data: providersData } = useQuery({ queryKey: ['account-providers'], queryFn: accountApi.providers })
  const selectionQuery = useQuery({
    queryKey: ['session-model', ctx.activeId],
    queryFn: () => accountApi.sessionModel(ctx.activeId!), enabled: !!ctx.activeId,
  })
  const [draftModel, setDraftModel] = useState<ModelSelection>()
  const [selectingModel, setSelectingModel] = useState(false)
  const selection = ctx.activeId ? selectionQuery.data : draftModel || providersData?.selection
  const activeProvider = selection?.valid ? providersData?.items.find(p => p.id === selection.provider_id && p.usable && p.models.some(m => m.model_id === selection.model_id)) : undefined
  async function selectModel(providerId: string, modelId: string) {
    setSelectingModel(true)
    try {
      if (ctx.activeId) {
        const value = await accountApi.selectSessionModel(ctx.activeId, providerId, modelId)
        modelQc.setQueryData(['session-model', ctx.activeId], value)
      } else {
        const provider = providersData?.items.find(p => p.id === providerId)
        const model = provider?.models.find(m => m.model_id === modelId)
        setDraftModel({ provider_id: providerId, model_id: modelId, provider_name: provider?.name, model_label: model?.display_name || modelId, valid: true })
      }
    } catch (error) { toast.error((error as Error).message) } finally { setSelectingModel(false) }
  }
  // 空状态下选择的 KB（第一次提问时用）
  const [selectedKbForNewSession, setSelectedKbForNewSession] = useState<string | null>(null)
  // 并发流式支持：sessionId -> turnId 映射（zustand 跨组件共享；切会话/离开聊天页不中断）
  const streamingMap = useAnswerStreams((s) => s.streaming)
  const [apiRetryCount] = useApiRetryCount()
  const [deepAiOptions] = useDeepAiOptions()
  const isStreaming = (sid: string | null | undefined): boolean =>
    !!sid && !!streamingMap[sid]

  // KB 列表查询
  const kbListQuery = useQuery({
    queryKey: ['kbs'],
    queryFn: api.kb.list,
    refetchOnWindowFocus: false,
  })
  const defaultKbQuery = useQuery({
    queryKey: ['settings', 'default_kb'],
    queryFn: api.defaultKb.get,
    staleTime: 60_000,
  })
  const currentKbId = session?.kb_scope || selectedKbForNewSession
  const currentKb = kbListQuery.data?.find(kb => kb.id === currentKbId)
  const currentKbLabel = kbLabel(currentKb) || (currentKbId ? '知识库不可用' : '请选择知识库')
  const canAskKb = !!currentKbId && !!kbListQuery.data?.some(kb => kb.id === currentKbId)

  // 初始化 selectedKbForNewSession：配置的默认 KB > is_default 字段
  useEffect(() => {
    if (!selectedKbForNewSession && kbListQuery.data) {
      const configured = defaultKbQuery.data?.kb_id
      if (configured && kbListQuery.data.some(kb => kb.id === configured)) {
        setSelectedKbForNewSession(configured)
        return
      }
      const fallback = kbListQuery.data.find(kb => kb.is_default)
      if (fallback) {
        setSelectedKbForNewSession(fallback.id)
      }
    }
  }, [kbListQuery.data, defaultKbQuery.data, selectedKbForNewSession])

  async function handleAsk(
    question: string,
    sessionId: string,
    history: ChatMessage[] | undefined,
    mode: RetrievalMode,
    resumeTurn?: ChatTurn,
  ) {
    const providerId = selection?.provider_id || undefined
    const modelId = selection?.model_id || undefined
    const turnId = resumeTurn?.id ?? createUuid()
    const turn: ChatTurn = {
      id: turnId,
      question,
      createdAt: Date.now(),
      mode,
      thinking: {
        stages: [],
        partialAnswer: '',
        status: 'streaming',
        startedAt: Date.now(),
        timerAnchor: performance.now(),
        timerBase: 0,
      },
    }
    const ac = new AbortController()
    useAnswerStreams.getState().start(sessionId, turnId, ac)
    // 本地累积已生成文本：流式中切走再切回（或 cache 被 refetch 重置）时的兜底真值
    let accumulated = ''
    let accumulatedSources: QaSource[] = []
    const accumulatedStages: QaTraceStage[] = []

    try {
      if (!resumeTurn && ['ai', 'deep_ai'].includes(mode)) {
        if (!activeProvider || !providerId || !modelId) throw new Error('请选择可用的 API 与模型')
        const saved = await accountApi.selectSessionModel(sessionId, providerId, modelId)
        modelQc.setQueryData(['session-model', sessionId], saved)
        setDraftModel(undefined)
      }
      if (!resumeTurn) await ctx.appendTurn(sessionId, turn)
      await api.qa.askStream(
        question,
        history,
        {
          onConnection: (message) => ctx.updateTurn(sessionId, turnId, t => patchThinking(t, th => ({ ...th, connectionMessage: message }))),
          onSnapshot: (snapshot) => {
            accumulated = snapshot.answer
            accumulatedSources = snapshot.sources
            accumulatedStages.splice(0, accumulatedStages.length, ...snapshot.trace)
            ctx.updateTurn(sessionId, turnId, t => ({
              ...patchThinking(t, th => ({ ...th, partialAnswer: snapshot.answer, stages: snapshot.trace,
                sources: snapshot.sources, ...snapshotTiming(snapshot.elapsed_ms, th), status: snapshot.state === 'running' ? 'streaming' : snapshot.state === 'cancelled' ? 'cancelled' : snapshot.state === 'done' ? 'done' : 'error' })),
              answer: snapshot.answer, sources: snapshot.sources, trace: snapshot.trace, error: snapshot.error || undefined,
            }))
          },
          onWarmup: (d) => {
            ctx.updateTurn(sessionId, turnId, (t) =>
              patchThinking(t, (th) => ({ ...th, warmup: d.msg })),
            )
          },
          onStage: (stage) => {
            // running 是阶段开始的瞬时标记，只进面板展示，不进兜底 trace
            if (stage.status !== 'running') accumulatedStages.push(stage)
            ctx.updateTurn(sessionId, turnId, (t) =>
              patchThinking(t, (th) => ({ ...th, stages: mergeStage(th.stages, stage) })),
            )
          },
          onSources: (sources) => {
            accumulatedSources = sources
            ctx.updateTurn(sessionId, turnId, (t) =>
              patchThinking(t, (th) => ({ ...th, sources })),
            )
          },
          onResults: (data) => {
            // 检索模式：把命中片段作为 sources 展示（done 事件会再带一次完整数据）
            ctx.updateTurn(sessionId, turnId, (t) => ({
              ...patchThinking(t, (th) => ({ ...th, sources: data.hits })),
              mode: data.mode,
              sources: data.hits,
            }))
          },
          onToken: (token) => {
            accumulated += token
            ctx.updateTurn(sessionId, turnId, (t) =>
              patchThinking(t, (th) => ({ ...th, partialAnswer: accumulated })),
            )
          },
          onDone: (data) => {
            // P1-13: abort 后即使后端发了 done 也不要 persistTurn（避免数据复活）
            if (ac.signal.aborted) return
            ctx.updateTurn(sessionId, turnId, (t) => ({
              ...patchThinking(t, (th) => ({
                ...th,
                stages: stripRunningStages(th.stages),
                status: data.outcome === 'partial' || data.verification === 'partial' ? 'partial' : 'done',
                elapsedMs: th.status === 'streaming' ? elapsedAnswer(th) : th.elapsedMs,
              })),
              answer: data.answer || '',
              sources: data.sources || [],
              trace: data.trace || [],
              usedProvider: data.used_provider,
            }))
            // 持久化（override 防止异步问题；检索模式 answer 为空但 sources 有命中）
            // 自动总结会话标题（异步，不阻塞主流程）
            // 不能依赖闭包里的 session（stale），要 fetch 最新状态
            void (async () => {
              try {
                const latest = await api.sessions.get(sessionId)
                if (resumeTurn || latest.title !== '新会话' || !data.answer) return
                const result = await api.qa.summarizeTitle(question, data.answer, providerId, sessionId, modelId)
                if (result.title) {
                  await ctx.renameSession(sessionId, result.title, true)
                }
              } catch {
                // 静默失败，不影响主流程
              }
            })()
          },
          onError: (error) => {
            if (ac.signal.aborted) return
            const message = error.message || '未知错误'
            const answer = error.partial || accumulated
            const trace = error.trace?.length ? error.trace : [...accumulatedStages, {
              stage: 'answer_result', label: '回答未完成', status: 'error', notes: message,
            }]
            ctx.updateTurn(sessionId, turnId, (t) => ({
              ...patchThinking(t, (th) => ({ ...th, stages: stripRunningStages(th.stages), status: 'error', elapsedMs: th.status === 'streaming' ? elapsedAnswer(th) : th.elapsedMs })),
              answer, trace, sources: accumulatedSources, error: message,
            }))

            // rebuild 进行中：toast 友好提示，让用户去看进度
            if ((error as any)?.rebuildInProgress) {
              toast.error('向量库重建中，请等待完成后再提问')
            }
          },

        },
        ac.signal,
        session?.kb_scope ?? selectedKbForNewSession ?? undefined,
        sessionId,
        turnId,
        topK,
        mode,
        providerId,
        undefined, // Legacy strict flag is superseded by constraint_strategy.
        apiRetryCount,
        deepAiOptions,
        'ask',
        modelId,
        !!resumeTurn,
      )
    } catch (error) {
      toast.error((error as Error).message || '无法开始生成')
      ctx.updateTurn(sessionId, turnId, t => ({ ...patchThinking(t, th => ({ ...th, status: 'error' })), error: (error as Error).message }))
    } finally {
      useAnswerStreams.getState().end(sessionId)
    }
  }

  const recoveryRef = useRef(new Set<string>())
  useEffect(() => {
    if (!session) return
    const sid = session.id
    if (recoveryRef.current.has(sid) || useAnswerStreams.getState().streaming[sid]) return
    let cancelled = false
    const recover = async () => {
      if (cancelled || recoveryRef.current.has(sid) || useAnswerStreams.getState().streaming[sid]) return
      try {
        const response = await apiFetch(answerJobPath(sid))
        if (!response.ok) return
        const { turn_ids: ids } = await response.json() as { turn_ids: string[] }
        if (cancelled) return
        recoveryRef.current.add(sid)
        // Only the latest turn can still run. Older turns are already persisted.
        const last = session.turns.at(-1)
        if (last && ids.includes(last.id) && !useAnswerStreams.getState().streaming[sid]) {
          void handleAsk(last.question, sid, undefined, last.mode ?? session.retrieval_mode ?? 'ai', last)
        }
      } catch { /* Retry discovery without creating a generation. */ }
    }
    void recover()
    const timer = setInterval(() => void recover(), 3000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [session?.id, session?.turns.length])

  function handleStop(sessionId?: string) {
    const sid = sessionId ?? ctx.activeId
    if (!sid) return
    abortAnswerStream(sid)
  }

  async function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault()
    const q = input.trim()
    if (!canAskKb) {
      toast.error(session ? '此知识库权限已撤销或知识库已删除。历史会话仍可查看，请新建对话并选择可用知识库。' : '请先创建或选择一个有权限的知识库。')
      return
    }
    if (['ai', 'deep_ai'].includes(session?.retrieval_mode ?? newSessionMode) && !activeProvider) {
      toast.error(selection?.reason || '请在输入框下方选择模型；没有可选项时请先配置 API。')
      return
    }
    // 只在"当前会话"有 stream 时禁止发新问题（其他会话的 stream 不影响）
    if (!q || isStreaming(ctx.activeId) || selectingModel) return
    // U-3: 超长问题前端预检（后端会 422，预检避免 error turn 残留）
    if (q.length > 4000) {
      toast.error(`问题过长（${q.length} 字，上限 4000），请精简后重试`)
      return
    }

    let sessionId = ctx.activeId
    let history: ChatMessage[] = []
    const selectedMode = session?.retrieval_mode ?? newSessionMode
    const mode: RetrievalMode = selectedMode === 'deep' ? 'basic' : selectedMode
    if (!sessionId) {
      try {
        sessionId = await ctx.createSession(selectedKbForNewSession || undefined, mode)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        toast.error(`创建会话失败，问题尚未发送：${message}`)
        return
      }
    } else if (session) {
      // The server loads the authorized session, including early history.
      history = []
    }

    scrollToBottom()
    setInput('')
    void handleAsk(q, sessionId, history.length > 0 ? history : undefined, mode).catch((error: unknown) => {
      toast.error(`发送失败：${error instanceof Error ? error.message : String(error)}`)
    })
  }

  // 部分完成/失败回答的一键重试：用原问题与原模式重新发起一轮
  function retryTurn(turn: ChatTurn) {
    const sid = ctx.activeId
    if (!sid || isStreaming(sid) || selectingModel) return
    const requested = turn.mode ?? 'basic'
    const mode: RetrievalMode = requested === 'deep' ? 'basic' : requested
    if (['ai', 'deep_ai'].includes(mode) && !activeProvider) {
      toast.error(selection?.reason || '请在输入框下方选择模型；没有可选项时请先配置 API。')
      return
    }
    scrollToBottom()
    void handleAsk(turn.question, sid, undefined, mode).catch((error: unknown) => {
      toast.error(`发送失败：${error instanceof Error ? error.message : String(error)}`)
    })
  }

  // 标题展示：没活动会话时显示"新对话"
  const title = session ? session.title : '新对话'
  const turns = session?.turns ?? []

  // 知识库选择：无会话=影响下一问；空会话=即时切换；有记录=走确认框新建会话
  const pickKb = (kbId: string) => {
    if (kbId === currentKbId || isStreaming(ctx.activeId)) return
    if (!session) {
      setSelectedKbForNewSession(kbId)
      return
    }
    if (turns.length === 0) {
      setSwitchingKb(true)
      ctx.updateSessionKb(session.id, kbId)
        .catch((error) => toast.error(error instanceof Error ? error.message : '切换失败，请重试'))
        .finally(() => setSwitchingKb(false))
      return
    }
    setPendingKbScope(kbId)
  }

  const { viewportRef, contentRef, showJump, scrollToBottom, jumpToTurn } = useChatScroll(ctx.activeId, turns.length > 0)

  // 空状态：保持原布局（标题 + 看板 + 输入框 + 示例问题）
  if (turns.length === 0) {
    return (
      <>
        <div className="mx-auto max-w-[820px] px-8 py-8">
          <div className="mb-6 flex items-start justify-between">
            <div className="min-w-0 flex-1 pr-3">
              <NewChatGreeting />
              {/* 空状态下显示当前选择的 KB：警示琥珀胶囊，提醒发问前核对 */}
              <div className="mt-4 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <Library className="h-3 w-3 shrink-0" />
                <span className="shrink-0 font-medium">知识库：</span>
                <KbPicker
                  kbs={kbListQuery.data}
                  value={currentKbId}
                  label={currentKbLabel}
                  onPick={pickKb}
                  variant="warn"
                  disabled={switchingKb || isStreaming(ctx.activeId)}
                  title="新对话将使用此知识库，发问前请核对"
                />
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="shrink-0 gap-1.5 text-[12px] text-muted-foreground"
              onClick={() => setShowStats(!showStats)}
            >
              <BarChart3 className="h-3.5 w-3.5" />
              {showStats ? '隐藏看板' : '显示看板'}
            </Button>
          </div>

          {showStats ? (
            <div className="mb-6">
              <StatsCards cardClassName="glass-content-card" />
            </div>
          ) : null}

          <form onSubmit={handleSubmit}>
            <Card className="chat-composer p-4">
              <AutosizeTextarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  // 回车发送；Shift/Alt/Ctrl/Cmd + 回车 = 换行；跳过输入法合成
                  if (
                    e.key === 'Enter' &&
                    !e.shiftKey &&
                    !e.altKey &&
                    !e.ctrlKey &&
                    !e.metaKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault()
                    handleSubmit()
                  }
                }}
                placeholder="请输入您的问题"
                className="min-h-[60px] w-full resize-none bg-transparent text-[14px] outline-none placeholder:text-muted-foreground"
              />
              <div className="mt-3 flex items-center justify-between border-t pt-3">
                <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                  {ctx.newGroupId && <span className="text-xs text-muted-foreground">分组：{ctx.groups.find(g => g.id === ctx.newGroupId)?.name ?? '未分组'}</span>}
              <RetrievalModeSelect
                    value={newSessionMode}
                    onChange={(v) => setNewSessionMode(v)}
                  />
                  <TopKSelect
                    key={countMode}
                    mode={currentMode}
                    value={topK}
                    onChange={(v) => setTopK(v)}
                  />
                  <ApiModelSelect compact providers={providersData?.items || []} selection={selection} disabled={selectingModel || isStreaming(ctx.activeId)} onChange={(p, m) => void selectModel(p, m)} />
                  <span className="inline-flex items-center gap-1 rounded bg-muted px-2 py-1 text-[11px] text-muted-foreground">
                    Enter 发送 · Shift+Enter 换行
                  </span>
                </div>
                <Button type="submit" size="sm" className="gap-1.5 text-[12px]" disabled={selectingModel || isStreaming(ctx.activeId) || !input.trim()}>
                  {isStreaming(ctx.activeId) ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <ArrowUp className="h-3.5 w-3.5" />
                  )}
                  <span>发送</span>
                </Button>
              </div>
            </Card>
          </form>
        </div>
      </>
    )
  }

  // 会话状态：ChatGPT 风格（输入框底部 + 消息流）
  return (
    <>
      <div className="flex h-full min-h-0 flex-col">
        <ChatSessionHeader key={ctx.activeId} knowledgeBase={currentKb?.name || currentKbLabel}>
          <div className="flex min-w-0 items-center gap-1.5">
            <button type="button" onClick={() => setShowSessionInfo(true)} className="mr-1 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent/30 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="会话信息" title="会话信息">
              <Info className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <Library className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <KbPicker
              kbs={kbListQuery.data}
              value={currentKbId}
              label={currentKbLabel}
              onPick={pickKb}
              variant="calm"
              disabled={switchingKb || isStreaming(ctx.activeId)}
              title={isStreaming(ctx.activeId) ? '请先完成或停止生成' : '换库将新建会话，原会话不动'}
            />
          </div>
        </ChatSessionHeader>
        {showSessionInfo && <ManagementDialog title="会话信息" onClose={() => setShowSessionInfo(false)} className="max-w-sm">
          <dl className="space-y-2 text-sm">
            <div><dt className="text-xs text-muted-foreground">会话</dt><dd>{title}</dd></div>
            <div><dt className="text-xs text-muted-foreground">知识库</dt><dd>{currentKbLabel}</dd></div>
            <div><dt className="text-xs text-muted-foreground">对话轮数</dt><dd>{turns.length} 轮</dd></div>
          </dl>
          <p className="text-xs leading-relaxed text-muted-foreground">多轮上下文会按当前配置与预算选取，不表示每次请求都会发送全部历史。换库会新建会话，原会话保留。</p>
        </ManagementDialog>}

        <div className="relative min-h-0 flex-1">
        <MessageNavigation key={ctx.activeId} turns={turns} viewportRef={viewportRef} contentRef={contentRef} onJump={jumpToTurn} />
        <div ref={viewportRef} tabIndex={0} aria-label="会话消息" className={`h-full overflow-y-auto overscroll-contain px-4 md:pl-8 md:pr-[max(0px,calc(2rem-var(--chat-scrollbar-width,0px)))] pb-6 md:pt-6 [overflow-anchor:none] ${turns.length > 1 ? 'pt-12' : 'pt-6'}`}>
          <div ref={contentRef} className="mx-auto max-w-[820px] space-y-5 pb-4">
            {turns.map((turn) => {
              const turnSessionId = ctx.activeId
              const streamingTurnId = turnSessionId ? streamingMap[turnSessionId] : undefined
              return (
                <TurnCard
                  key={turn.id}
                  turn={turn}
                  searchResults={<SearchTurnResults turn={turn} sessionId={turnSessionId!}
                    kbScope={session?.kb_scope ?? undefined} topK={countFor('basic')}
                    canExpand={canAskKb && !streamingTurnId}
                    onSaved={expansion => ctx.updateTurn(turnSessionId!, turn.id, t => ({ ...t, expansion }))} />}
                  onStop={streamingTurnId === turn.id ? () => handleStop(turnSessionId ?? undefined) : undefined}
                  onRetry={!streamingTurnId ? () => retryTurn(turn) : undefined}
                />
              )
            })}
          </div>
        </div>
        {showJump && <Button type="button" variant="outline" size="sm" onClick={scrollToBottom}
          className="absolute bottom-3 left-1/2 -translate-x-1/2 gap-1.5 rounded-full bg-popover shadow-md">
          <ArrowDown className="h-3.5 w-3.5" />回到底部
        </Button>}
        </div>

        <div className="shrink-0 px-4 pb-4 pt-2 md:px-8">
          {!canAskKb && <p className="mx-auto mb-3 max-w-[820px] text-sm text-muted-foreground">当前知识库已不可用，历史会话保留。请新建对话并选择有权限的知识库。</p>}
          <form onSubmit={handleSubmit} className="mx-auto max-w-[820px]">
            <Card className="chat-composer p-3">
              <AutosizeTextarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  // 回车发送；Shift/Alt/Ctrl/Cmd + 回车 = 换行；跳过输入法合成
                  if (
                    e.key === 'Enter' &&
                    !e.shiftKey &&
                    !e.altKey &&
                    !e.ctrlKey &&
                    !e.metaKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault()
                    handleSubmit()
                  }
                }}
                placeholder="请输入您的问题"
                className="min-h-[28px] w-full resize-none bg-transparent text-[14px] leading-tight outline-none placeholder:text-muted-foreground"
              />
              <div className="mt-3 flex items-center justify-between gap-2">
                <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                  <RetrievalModeSelect
                    value={currentMode}
                    onChange={(v) => {
                      if (session) void ctx.updateSessionMode(session.id, v)
                    }}
                  />
                  <TopKSelect
                    key={countMode}
                    mode={currentMode}
                    value={topK}
                    onChange={(v) => setTopK(v)}
                  />
                  <ApiModelSelect compact providers={providersData?.items || []} selection={selection} disabled={selectingModel || isStreaming(ctx.activeId)} onChange={(p, m) => void selectModel(p, m)} />
                </div>
                <Button type="submit" size="sm" className="gap-1.5 text-[12px]" disabled={selectingModel || isStreaming(ctx.activeId) || !input.trim()}>
                  {isStreaming(ctx.activeId) ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <ArrowUp className="h-3.5 w-3.5" />
                  )}
                  <span>发送</span>
                </Button>
              </div>
            </Card>
          </form>
        </div>
      </div>

      {/* KB 切换确认弹窗：有问答记录的会话换库时新建会话 */}
      {pendingKbScope && (
        <ManagementDialog title="换库新建会话" onClose={() => setPendingKbScope(null)} busy={switchingKb} className="max-w-lg">

            <p className="text-sm text-muted-foreground mb-4">
              新会话从空白开始，使用「{kbLabel(kbListQuery.data?.find(kb => kb.id === pendingKbScope))}」。原会话不迁移、不继承。
            </p>

            <div className="flex justify-end gap-2">
              <Button variant="outline" disabled={switchingKb} onClick={() => setPendingKbScope(null)}>
                取消
              </Button>
              <Button
                disabled={switchingKb}
                onClick={async () => {
                  if (ctx.activeId && pendingKbScope && !isStreaming(ctx.activeId)) {
                    setSwitchingKb(true)
                    try {
                      await ctx.createSession(pendingKbScope, countMode)
                      setSelectedKbForNewSession(pendingKbScope)
                      setPendingKbScope(null)
                    } catch (error) {
                      toast.error(error instanceof Error ? error.message : '新建失败，当前会话已保留')
                    } finally {
                      setSwitchingKb(false)
                    }
                  }
                }}
              >
                {switchingKb ? '创建中…' : '新建会话'}
              </Button>
            </div>
          </ManagementDialog>
      )}
    </>
  )
}

function TurnCard({ turn, onStop, onRetry, searchResults }: { turn: ChatTurn; onStop?: () => void; onRetry?: () => void; searchResults: React.ReactNode }) {
  const [copied, setCopied] = useState(false)
  const [layout] = useLocalStorage<string>('amd-chat-layout', 'classic')
  const bubble = layout === 'bubble'
  const [showThinking] = useLocalStorage<boolean>('amd-ui-show-thinking', true)
  const [showCitations] = useLocalStorage<boolean>('amd-ui-show-citations', false)
  const [showContext] = useLocalStorage<boolean>('amd-ui-show-context', false)
  const citationPrefix = `citation-${useId()}`
  const [citationSelection, setCitationSelection] = useState<{ number: number; request: number } | null>(null)
  const thinkingStatus = turn.thinking?.status
  const isStreaming = thinkingStatus === 'streaming'
  const isSearchMode = turn.mode === 'basic' || turn.mode === 'deep'
  const stages = !isStreaming && turn.trace?.length ? turn.trace : turn.thinking?.stages ?? []
  const resultStage = stages.findLast(stage => stage.stage === 'answer_result')
  const generationStage = stages.findLast(stage => stage.stage === 'generation')
  const restoredStatus = resultStage?.status === 'partial' || generationStage?.status === 'partial'
    ? 'partial'
    : resultStage?.status === 'cancelled' ? 'cancelled' : turn.error ? 'error' : 'done'
  const thinking: ThinkingState = {
    ...turn.thinking,
    stages,
    status: thinkingStatus ?? restoredStatus,
    startedAt: turn.thinking?.startedAt ?? 0,
    partialAnswer: turn.thinking?.partialAnswer ?? '',
  }

  return (
    <article data-turn-id={turn.id} className={`min-w-0 ${bubble ? 'space-y-5 pb-6' : 'space-y-1'}`} aria-label="一轮问答">
      <div className={bubble ? "chat-question-bubble ml-auto w-fit min-w-0 max-w-[92%] rounded-2xl border border-border/60 bg-secondary px-4 py-3" : "chat-bubble min-w-0 rounded-t-xl rounded-b-none border bg-secondary/60 px-5 py-3"}>
        {!bubble && <span className="mb-1 block text-[10px] font-medium text-muted-foreground">你</span>}
        <div className="min-w-0 flex-1 text-[13px] font-medium leading-relaxed [overflow-wrap:anywhere]">{turn.question}</div>
      </div>

      <div className={bubble ? "min-w-0 px-1 py-2" : "chat-bubble min-w-0 rounded-t-none rounded-b-xl border bg-card/80 px-5 py-4"}>
        <span className="mb-2 block text-[10px] font-medium text-muted-foreground">{isSearchMode ? '基础检索' : turn.mode === 'deep_ai' ? '深度AI' : '增强AI'}</span>
        <div className="min-w-0 flex-1">
          {isStreaming || (showThinking && stages.length > 0 && !(isSearchMode && (turn.expansion || turn.mode === 'deep'))) ? (
            <ThinkingPanel thinking={thinking} onStop={onStop} showDetails={showThinking} showCitations={showCitations} />
          ) : null}
          {showContext && stages.some(stage => stage.stage === 'conversation_context') && <details className="mb-3 rounded-md border bg-muted/20 p-3 text-xs text-muted-foreground"><summary className="cursor-pointer font-medium">上下文使用情况</summary><p className="mt-2 leading-relaxed">{stages.findLast(stage => stage.stage === 'conversation_context')?.notes}</p><p className="mt-1">本轮历史查阅 {stages.filter(stage => ['搜索会话历史', '阅读历史问答'].includes(stage.label)).length} 次</p></details>}
          {!isStreaming && turn.error ? (
            <div className="flex items-center gap-2 text-destructive text-[12px]">
              <AlertCircle className="h-4 w-4" />
              {turn.error}
            </div>
          ) : null}
          {!isStreaming && !turn.error && thinking.status === 'partial' && (() => {
            const note = resultStage?.notes || generationStage?.notes || ''
            const reason = note.replace(/^provider=\S+；?/, '').trim() || '回答未完整生成'
            return (
              <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-[12px] text-warning">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                <span className="min-w-0 flex-1">部分完成 · {reason}，已保留可用部分，请结合引用原文核对。</span>
                {onRetry && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-6 shrink-0 border-warning/40 bg-transparent px-2 text-[11px] text-warning hover:bg-warning/15 hover:text-warning"
                    onClick={onRetry}
                  >
                    重试本次提问
                  </Button>
                )}
              </div>
            )
          })()}
          {!isStreaming && !turn.error && isSearchMode ? (
            searchResults
          ) : null}
          {!isStreaming && (!turn.error || !!turn.answer) && !isSearchMode && (
            <>
              <MarkdownContent
                reading
                content={turn.answer || ''}
                hideCitations={!showCitations}
                citationCount={turn.sources?.length ?? 0}
                citationPrefix={citationPrefix}
                onCitationClick={number => setCitationSelection(previous => ({ number, request: (previous?.request ?? 0) + 1 }))}
              />
              <div className="mt-4">
                <button
                  type="button"
                  onClick={() => {
                    if (turn.answer) {
                      navigator.clipboard.writeText(turn.answer.replace(/【cite:([1-9]\d*)】/g, showCitations ? "[$1]" : ""))
                      setCopied(true)
                      setTimeout(() => setCopied(false), 1500)
                    }
                  }}
                  className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                >
                  {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  {copied ? '已复制' : '复制回答'}
                </button>
              </div>
            </>
          )}
          {!isSearchMode && showCitations && (
            <CitationSources sources={turn.sources ?? []} prefix={citationPrefix} selection={citationSelection} />
          )}
          {!!turn.createdAt && <time dateTime={new Date(turn.createdAt).toISOString()} className="mt-3 block text-[10px] text-muted-foreground">{new Date(turn.createdAt).toLocaleString('zh-CN', { hour12: false })}</time>}
        </div>
      </div>
    </article>
  )
}
