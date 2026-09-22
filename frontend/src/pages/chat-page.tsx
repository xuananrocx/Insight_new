import { useEffect, useId, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  ArrowUp,
  Zap,
  Library,
  Loader2,
  AlertCircle,
  BarChart3,
  MessageSquarePlus,
  Copy,
  Check,
  ChevronRight,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { StatsCards } from '@/components/stats-cards'
import { MarkdownContent } from '@/components/markdown-content'
import { ThinkingPanel } from '@/components/thinking-panel'
import { CitationSources } from '@/components/citation-sources'
import { TopKSelect } from '@/components/top-k-select'
import { RetrievalModeSelect } from '@/components/retrieval-mode-select'
import { SearchResultsList } from '@/components/search-results-list'
import { useChatSessionsCtx } from '@/hooks/chat-session-context'
import { useLocalStorage } from '@/hooks/use-local-storage'
import { type ChatTurn, type ThinkingState } from '@/hooks/use-chat-sessions'
import { api, type ChatMessage, type RetrievalMode, type SearchHit, type QaSource, type QaTraceStage } from '@/lib/api'
import { useAnswerStreams, abortAnswerStream } from '@/stores/answer-streams'

// thinking 重建守卫：流式期间若 turnsState / detail cache 被 refetch 重置导致 thinking 丢失，
// 从回调现场重建，避免后续 token 被 `t.thinking ?` 守卫静默丢弃
function patchThinking(t: ChatTurn, patch: (th: ThinkingState) => ThinkingState): ChatTurn {
  const base: ThinkingState =
    t.thinking ?? { stages: [], partialAnswer: '', status: 'streaming', startedAt: Date.now() }
  return { ...t, thinking: patch(base) }
}

export function ChatPage() {
  const ctx = useChatSessionsCtx()
  const session = ctx.activeSession
  const [input, setInput] = useState('')
  const [topK, setTopK] = useState(10)
  // 空状态（无会话）下选的检索模式，首问建会话时带过去
  const [newSessionMode, setNewSessionMode] = useState<RetrievalMode>('ai')
  const [showStats, setShowStats] = useLocalStorage('amd-ui-show-stats', true)
  const [showKbSwitch, setShowKbSwitch] = useState(false)
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

  const { data: providersData, isLoading: providersLoading } = useQuery({
    queryKey: ['llm-providers'],
    queryFn: () => api.llm.providers(),
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  })
  const activeProvider = providersData?.providers.find(
    (p) => p.name === providersData?.current_provider,
  )
  const activeModelLabel =
    activeProvider?.chat_model || providersData?.current_provider || '未配置'
  // 空状态下选择的 KB（第一次提问时用）
  const [selectedKbForNewSession, setSelectedKbForNewSession] = useState<string | null>(null)
  // 并发流式支持：sessionId -> turnId 映射（zustand 跨组件共享；切会话/离开聊天页不中断）
  const streamingMap = useAnswerStreams((s) => s.streaming)
  const [strictKnowledge] = useLocalStorage('amd-ai-strict-knowledge', false)
  const [apiRetryCount] = useLocalStorage('amd-ai-api-retry-count', 5)
  const isStreaming = (sid: string | null | undefined): boolean =>
    !!sid && !!streamingMap[sid]

  const titleMutation = useMutation({
    mutationFn: ({ question, answer }: { question: string; answer: string }) =>
      api.qa.summarizeTitle(question, answer),
  })

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
  ) {
    const providerId = providersData?.current_provider
    const turnId = crypto.randomUUID()
    const turn: ChatTurn = {
      id: turnId,
      question,
      mode,
      thinking: {
        stages: [],
        partialAnswer: '',
        status: 'streaming',
        startedAt: Date.now(),
      },
    }
    const ac = new AbortController()
    useAnswerStreams.getState().start(sessionId, turnId, ac)
    // 本地累积已生成文本：流式中切走再切回（或 cache 被 refetch 重置）时的兜底真值
    let accumulated = ''
    let accumulatedSources: QaSource[] = []
    const accumulatedStages: QaTraceStage[] = []

    try {
      await ctx.appendTurn(sessionId, turn)
      await api.qa.askStream(
        question,
        history,
        {
          onWarmup: (d) => {
            ctx.updateTurn(sessionId, turnId, (t) =>
              patchThinking(t, (th) => ({ ...th, warmup: d.msg })),
            )
          },
          onStage: (stage) => {
            accumulatedStages.push(stage)
            ctx.updateTurn(sessionId, turnId, (t) =>
              patchThinking(t, (th) => ({ ...th, stages: [...th.stages, stage] })),
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
                status: data.outcome === 'partial' || data.verification === 'partial' ? 'partial' : 'done',
                elapsedMs: Date.now() - th.startedAt,
              })),
              answer: data.answer || '',
              sources: data.sources || [],
              trace: data.trace || [],
              usedProvider: data.used_provider,
            }))
            // 持久化（override 防止异步问题；检索模式 answer 为空但 sources 有命中）
            ctx.persistTurn(sessionId, turnId, {
              answer: data.answer,
              sources: data.sources,
              trace: data.trace,
              usedProvider: data.used_provider,
            })
            // 自动总结会话标题（异步，不阻塞主流程）
            // 不能依赖闭包里的 session（stale），要 fetch 最新状态
            void (async () => {
              try {
                const latest = await api.sessions.get(sessionId)
                if (latest.title !== '新会话' || !data.answer) return
                const result = await api.qa.summarizeTitle(question, data.answer, providerId, sessionId)
                if (result.title) {
                  await ctx.renameSession(sessionId, result.title)
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
              ...patchThinking(t, (th) => ({ ...th, status: 'error', elapsedMs: Date.now() - th.startedAt })),
              answer, trace, sources: accumulatedSources, error: message,
            }))
            void ctx.persistTurn(sessionId, turnId, { answer, trace, sources: accumulatedSources, error: message })
            // rebuild 进行中：toast 友好提示，让用户去看进度
            if ((error as any)?.rebuildInProgress) {
              toast.error('向量库重建中，请等待完成后再提问')
            }
          },
          onCancelled: () => {
            const trace = [...accumulatedStages, { stage: 'answer_result', label: '已停止回答', status: 'cancelled', notes: '用户已停止生成，保留已收到的内容' }]
            // 主动 abort（停止按钮/切 KB/删除会话）：保留已生成内容并落库；
            // 用本地累积值而非 activeSession（中断时会话可能已切走，activeSession 找不到该 turn）
            ctx.updateTurn(sessionId, turnId, (t) => ({
              ...patchThinking(t, (th) => ({
                ...th,
                partialAnswer: accumulated,
                status: 'cancelled',
                elapsedMs: Date.now() - th.startedAt,
              })),
              // 已有部分答案：保留并标记完成；无答案：标记已取消
              answer: accumulated || t.answer,
              error: '已停止生成',
              trace,
              sources: accumulatedSources,
            }))
            void ctx.persistTurn(sessionId, turnId, {
                answer: accumulated,
                sources: accumulatedSources,
                trace,
                error: '已停止生成',
                usedProvider: undefined,
              })
          },
        },
        ac.signal,
        session?.kb_scope ?? selectedKbForNewSession ?? undefined,
        sessionId,
        turnId,
        topK,
        mode,
        providerId,
        strictKnowledge,
        apiRetryCount,
      )
    } finally {
      useAnswerStreams.getState().end(sessionId)
    }
  }

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
      toast.error('请先在设置中添加并选择个人 API，或选择管理员授权的团队 API。')
      return
    }
    // 只在"当前会话"有 stream 时禁止发新问题（其他会话的 stream 不影响）
    if (!q || isStreaming(ctx.activeId)) return
    // U-3: 超长问题前端预检（后端会 422，预检避免 error turn 残留）
    if (q.length > 4000) {
      toast.error(`问题过长（${q.length} 字，上限 4000），请精简后重试`)
      return
    }

    let sessionId = ctx.activeId
    let history: ChatMessage[] = []
    const mode: RetrievalMode = session?.retrieval_mode ?? newSessionMode
    if (!sessionId) {
      try {
        sessionId = await ctx.createSession(selectedKbForNewSession || undefined, mode)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        toast.error(`创建会话失败，问题尚未发送：${message}`)
        return
      }
    } else if (session) {
      const recentTurns = session.turns.slice(-6)
      for (const t of recentTurns) {
        if (t.question && t.answer) {
          history.push({ role: 'user', content: t.question })
          history.push({ role: 'assistant', content: t.answer })
        }
      }
    }

    setInput('')
    void handleAsk(q, sessionId, history.length > 0 ? history : undefined, mode).catch((error: unknown) => {
      toast.error(`发送失败：${error instanceof Error ? error.message : String(error)}`)
    })
  }

  // 标题展示：没活动会话时显示"新对话"
  const title = session ? session.title : '新对话'
  const turns = session?.turns ?? []

  // 自动滚动到底部（新消息出现时）
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (turns.length > 0) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [turns.length, turns[turns.length - 1]?.answer])

  // 空状态：保持原布局（标题 + 看板 + 输入框 + 示例问题）
  if (turns.length === 0) {
    return (
      <>
        <div className="mx-auto max-w-4xl px-8 py-8">
          <div className="mb-6 flex items-start justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-[22px] font-semibold tracking-tight">
                {title}
              </h1>
              <p className="mt-1 text-[13px] text-muted-foreground">
                基于 RAG 的智能问答 · 让 AI 基于知识库回答
              </p>
              {/* 空状态下显示当前选择的 KB */}
              <div className="mt-2 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <Library className="h-3 w-3" />
                <span className="font-medium">知识库：</span>
                <span className="text-accent-foreground">
                  {kbListQuery.data?.find(kb => kb.id === selectedKbForNewSession)?.name ||
                   kbListQuery.data?.find(kb => kb.is_default)?.name ||
                   '默认'}
                </span>
                <button
                  type="button"
                  onClick={() => setShowKbSwitch(true)}
                  className="ml-1 rounded hover:bg-accent/30 px-1 py-0.5 transition-colors"
                  title="选择知识库"
                >
                  <ChevronRight className="h-3 w-3" />
                </button>
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="gap-1.5 text-[12px] text-muted-foreground"
              onClick={() => setShowStats(!showStats)}
            >
              <BarChart3 className="h-3.5 w-3.5" />
              {showStats ? '隐藏看板' : '显示看板'}
            </Button>
          </div>

          {showStats ? (
            <div className="mb-6">
              <StatsCards />
            </div>
          ) : null}

          <form onSubmit={handleSubmit}>
            <Card className="p-4">
              <textarea
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
                  <RetrievalModeSelect
                    value={newSessionMode}
                    onChange={(v) => setNewSessionMode(v)}
                  />
                  <TopKSelect
                    value={topK}
                    onChange={(v) => setTopK(v)}
                  />
                  <span
                    className="ml-2 inline-flex items-center gap-1 rounded bg-muted px-2 py-1 text-[11px] text-muted-foreground"
                    title={`当前模型：${activeProvider?.name || '—'} · ${activeProvider?.chat_model || '—'}`}
                  >
                    <Zap className="h-3 w-3" />
                    {providersLoading ? '…' : activeModelLabel}
                  </span>
                  <span className="inline-flex items-center gap-1 rounded bg-muted px-2 py-1 text-[11px] text-muted-foreground">
                    Enter 发送 · Shift+Enter 换行
                  </span>
                </div>
                <Button type="submit" size="sm" className="gap-1.5 text-[12px]" disabled={isStreaming(ctx.activeId) || !input.trim()}>
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

        {/* KB 选择弹窗（空状态和会话状态共用） */}
        {showKbSwitch && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
            <div className="bg-popover rounded-lg shadow-lg w-full max-w-md p-6">
              <h2 className="text-lg font-semibold mb-2">
                选择知识库
              </h2>
              <p className="text-sm text-muted-foreground mb-4">
                选择用于新对话的知识库
              </p>

              <div className="space-y-2 mb-6 max-h-[300px] overflow-y-auto">
                {kbListQuery.data?.map((kb) => (
                  <button
                    key={kb.id}
                    type="button"
                    onClick={() => {
                      setSelectedKbForNewSession(kb.id)
                      setShowKbSwitch(false)
                    }}
                    className={`w-full text-left p-3 rounded-md border transition-colors ${
                      selectedKbForNewSession === kb.id
                        ? 'border-primary bg-accent/30'
                        : 'hover:bg-accent/30'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <Library className="h-4 w-4 text-muted-foreground" />
                      <span className="font-medium">{kb.name}</span>
                      {kb.is_default && (
                        <span className="text-xs text-muted-foreground">（默认）</span>
                      )}
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {kb.document_count || 0} 个文档
                    </div>
                  </button>
                ))}
              </div>

              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => setShowKbSwitch(false)}>
                  取消
                </Button>
              </div>
            </div>
          </div>
        )}
      </>
    )
  }

  // 会话状态：ChatGPT 风格（输入框底部 + 消息流）
  return (
    <>
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between border-b border-white/10 px-6 py-3">
          <div className="min-w-0">
            <h1 className="flex items-center gap-2 text-[16px] font-semibold tracking-tight">
              <span className="truncate">{title}</span>
              {titleMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
              ) : null}
            </h1>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              已对话 {turns.length} 轮 · 多轮上下文
            </p>
            <div className="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Library className="h-3 w-3" />
              <span className="font-medium">知识库：</span>
              <span className="text-accent-foreground">
                {kbListQuery.data?.find(kb => kb.id === session?.kb_scope)?.name || session?.kb_scope || '默认'}
              </span>
              <button
                type="button"
                onClick={() => setShowKbSwitch(true)}
                className="ml-1 rounded hover:bg-accent/30 px-1 py-0.5 transition-colors"
                title="切换知识库"
              >
                <ChevronRight className="h-3 w-3" />
              </button>
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0 gap-1.5 text-[12px]"
            onClick={() => ctx.clearActive()}
            title="开始新对话"
          >
            <MessageSquarePlus className="h-3.5 w-3.5" />
            新对话
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto px-8 py-6">
          <div className="mx-auto max-w-4xl space-y-4 pb-4">
            {turns.map((turn) => {
              const turnSessionId = ctx.activeId
              const streamingTurnId = turnSessionId ? streamingMap[turnSessionId] : undefined
              return (
                <TurnCard
                  key={turn.id}
                  turn={turn}
                  onStop={streamingTurnId === turn.id ? () => handleStop(turnSessionId ?? undefined) : undefined}
                />
              )
            })}
            <div ref={bottomRef} />
          </div>
        </div>

        <div className="border-t border-white/10 px-8 py-3">
          {!canAskKb && <p className="mx-auto mb-3 max-w-4xl text-sm text-muted-foreground">当前知识库已不可用，历史会话保留。请新建对话并选择有权限的知识库。</p>}
          <form onSubmit={handleSubmit} className="mx-auto max-w-4xl">
            <Card className="p-3">
              <textarea
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
                    value={session?.retrieval_mode ?? 'ai'}
                    onChange={(v) => {
                      if (session) void ctx.updateSessionMode(session.id, v)
                    }}
                  />
                  <TopKSelect
                    value={topK}
                    onChange={(v) => setTopK(v)}
                  />
                </div>
                <Button type="submit" size="sm" className="gap-1.5 text-[12px]" disabled={isStreaming(ctx.activeId) || !input.trim()}>
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

      {/* KB 切换弹窗（组件级别，空状态和会话状态共用） */}
      {showKbSwitch && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-popover rounded-lg shadow-lg w-full max-w-md p-6">
            <h2 className="text-lg font-semibold mb-2">
              {session ? '切换知识库' : '选择知识库'}
            </h2>
            <p className="text-sm text-muted-foreground mb-4">
              {session
                ? '切换知识库将清空当前会话的问答历史（会话本身保留）'
                : '选择用于新对话的知识库'}
            </p>

            <div className="space-y-2 mb-6 max-h-[300px] overflow-y-auto">
              {kbListQuery.data?.map((kb) => (
                <button
                  key={kb.id}
                  type="button"
                  onClick={() => {
                    if (session) {
                      // 会话状态：设置待切换的 KB，弹出确认框
                      setPendingKbScope(kb.id)
                      setShowKbSwitch(false)
                    } else {
                      // 空状态：直接设置选择的 KB
                      setSelectedKbForNewSession(kb.id)
                      setShowKbSwitch(false)
                    }
                  }}
                  className={`w-full text-left p-3 rounded-md border transition-colors ${
                    (session?.kb_scope === kb.id) ||
                    (!session && selectedKbForNewSession === kb.id)
                      ? 'border-primary bg-accent/30'
                      : 'hover:bg-accent/30'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Library className="h-4 w-4 text-muted-foreground" />
                    <span className="font-medium">{kb.name}</span>
                    {kb.is_default && (
                      <span className="text-xs text-muted-foreground">（默认）</span>
                    )}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {kb.document_count || 0} 个文档
                  </div>
                </button>
              ))}
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setShowKbSwitch(false)}>
                取消
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* KB 切换确认弹窗 */}
      {pendingKbScope && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-popover rounded-lg shadow-lg w-full max-w-sm p-6">
            <h2 className="text-lg font-semibold mb-2">确认切换知识库</h2>
            <p className="text-sm text-muted-foreground mb-4">
              切换知识库将清空当前会话的问答历史，会话本身会保留。继续？
            </p>

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setPendingKbScope(null)}>
                取消
              </Button>
              <Button
                disabled={switchingKb}
                onClick={async () => {
                  if (ctx.activeId && pendingKbScope) {
                    setSwitchingKb(true)
                    try {
                      // 1. 先 abort 进行中的流（避免僵尸 turn 写回新 KB）
                      handleStop(ctx.activeId)
                      // 2. 切 KB + 清后端 turns + 清前端 turns
                      await ctx.updateSessionKb(ctx.activeId, pendingKbScope)
                      ctx.clearTurns()
                      setPendingKbScope(null)
                    } finally {
                      setSwitchingKb(false)
                    }
                  }
                }}
              >
                {switchingKb ? '切换中…' : '确认切换'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function TurnCard({ turn, onStop }: { turn: ChatTurn; onStop?: () => void }) {
  const [copied, setCopied] = useState(false)
  const [showThinking] = useLocalStorage<boolean>('amd-ui-show-thinking', true)
  const [showCitations] = useLocalStorage<boolean>('amd-ui-show-citations', false)
  const citationPrefix = `citation-${useId()}`
  const [citationSelection, setCitationSelection] = useState<{ number: number; request: number } | null>(null)
  const thinkingStatus = turn.thinking?.status
  const isStreaming = thinkingStatus === 'streaming'
  const isSearchMode = turn.mode === 'basic' || turn.mode === 'deep'
  const stages = !isStreaming && turn.trace?.length ? turn.trace : turn.thinking?.stages ?? []
  const resultStage = stages.findLast(stage => stage.stage === 'answer_result')
  const restoredStatus = resultStage?.status === 'partial' ? 'partial' : resultStage?.status === 'cancelled' ? 'cancelled' : turn.error ? 'error' : 'done'
  const thinking: ThinkingState = {
    ...turn.thinking,
    stages,
    status: thinkingStatus ?? restoredStatus,
    startedAt: turn.thinking?.startedAt ?? 0,
    partialAnswer: turn.thinking?.partialAnswer ?? '',
  }

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-start gap-2">
        <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded bg-secondary text-[10px] font-medium text-secondary-foreground">
          Q
        </span>
        <div className="min-w-0 flex-1 text-[13px] font-medium leading-relaxed [overflow-wrap:anywhere]">{turn.question}</div>
      </div>

      <div className="flex items-start gap-2">
        <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded bg-primary text-[10px] font-medium text-primary-foreground">
          A
        </span>
        <div className="min-w-0 flex-1">
          {isStreaming || (showThinking && stages.length > 0) ? (
            <ThinkingPanel thinking={thinking} onStop={onStop} showDetails={showThinking} showCitations={showCitations} />
          ) : null}
          {!isStreaming && turn.error ? (
            <div className="flex items-center gap-2 text-destructive text-[12px]">
              <AlertCircle className="h-4 w-4" />
              {turn.error}
            </div>
          ) : null}
          {!isStreaming && !turn.error && resultStage?.status === 'partial' && <p className="mb-2 text-xs text-muted-foreground">部分完成 · {resultStage.notes || '回答未完整生成'}</p>}
          {!isStreaming && !turn.error && isSearchMode ? (
            <SearchResultsList
              hits={(turn.sources ?? []) as SearchHit[]}
              question={turn.question}
            />
          ) : null}
          {!isStreaming && (!turn.error || !!turn.answer) && !isSearchMode && (
            <>
              <MarkdownContent
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
                      navigator.clipboard.writeText(turn.answer)
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
        </div>
      </div>

      {!isSearchMode && showCitations && (
        <CitationSources sources={turn.sources ?? []} prefix={citationPrefix} selection={citationSelection} />
      )}
    </Card>
  )
}
