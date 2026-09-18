import { useEffect, useRef, useState } from 'react'
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
import { TopKSelect } from '@/components/top-k-select'
import { RetrievalModeSelect } from '@/components/retrieval-mode-select'
import { SearchResultsList } from '@/components/search-results-list'
import { useChatSessionsCtx } from '@/hooks/chat-session-context'
import { useLocalStorage } from '@/hooks/use-local-storage'
import { type ChatTurn } from '@/hooks/use-chat-sessions'
import { api, type ChatMessage, type RetrievalMode, type SearchHit } from '@/lib/api'

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
  // 并发流式支持：sessionId -> turnId 映射
  const [streamingMap, setStreamingMap] = useState<Record<string, string>>({})
  const abortRefs = useRef<Map<string, AbortController>>(new Map())
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

  // 注册"切换会话前"回调：abort 当前所有进行中的 SSE（除了即将切换到的目标）
  // 流式中切会话：旧 SSE 应停止推送 token 到 cache，避免资源泄漏 + 数据错乱
  useEffect(() => {
    ctx.registerBeforeSelect((newId) => {
      for (const [sid, ac] of abortRefs.current.entries()) {
        if (sid !== newId) {
          try {
            ac.abort()
          } catch (e) {
            console.warn('abort on select failed', e)
          }
          abortRefs.current.delete(sid)
          setStreamingMap((prev) => {
            const next = { ...prev }
            delete next[sid]
            return next
          })
        }
      }
    })
  }, [ctx])

  async function handleAsk(
    question: string,
    sessionId: string,
    history: ChatMessage[] | undefined,
    mode: RetrievalMode,
  ) {
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
    ctx.appendTurn(sessionId, turn)
    setStreamingMap((prev) => ({ ...prev, [sessionId]: turnId }))

    const ac = new AbortController()
    abortRefs.current.set(sessionId, ac)

    try {
      await api.qa.askStream(
        question,
        history,
        {
          onWarmup: (d) => {
            ctx.updateTurn(sessionId, turnId, (t) => ({
              ...t,
              thinking: t.thinking ? { ...t.thinking, warmup: d.msg } : t.thinking,
            }))
          },
          onStage: (stage) => {
            ctx.updateTurn(sessionId, turnId, (t) => ({
              ...t,
              thinking: t.thinking
                ? { ...t.thinking, stages: [...t.thinking.stages, stage] }
                : t.thinking,
            }))
          },
          onSources: (sources) => {
            ctx.updateTurn(sessionId, turnId, (t) => ({
              ...t,
              thinking: t.thinking ? { ...t.thinking, sources } : t.thinking,
            }))
          },
          onResults: (data) => {
            // 检索模式：把命中片段作为 sources 展示（done 事件会再带一次完整数据）
            ctx.updateTurn(sessionId, turnId, (t) => ({
              ...t,
              mode: data.mode,
              sources: data.hits,
              thinking: t.thinking ? { ...t.thinking, sources: data.hits } : t.thinking,
            }))
          },
          onToken: (token) => {
            ctx.updateTurn(sessionId, turnId, (t) => ({
              ...t,
              thinking: t.thinking
                ? { ...t.thinking, partialAnswer: t.thinking.partialAnswer + token }
                : t.thinking,
            }))
          },
          onDone: (data) => {
            // P1-13: abort 后即使后端发了 done 也不要 persistTurn（避免数据复活）
            const ac = abortRefs.current.get(sessionId)
            if (ac?.signal.aborted) {
              setStreamingMap((prev) => {
                const next = { ...prev }
                delete next[sessionId]
                return next
              })
              abortRefs.current.delete(sessionId)
              return
            }
            ctx.updateTurn(sessionId, turnId, (t) => ({
              ...t,
              answer: data.answer || '',
              sources: data.sources || [],
              trace: data.trace || [],
              thinking: t.thinking
                ? { ...t.thinking, status: 'done', elapsedMs: Date.now() - t.thinking!.startedAt }
                : t.thinking,
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
                const result = await api.qa.summarizeTitle(question, data.answer)
                if (result.title) {
                  await ctx.renameSession(sessionId, result.title)
                }
              } catch {
                // 静默失败，不影响主流程
              }
            })()
          },
          onError: (error) => {
            ctx.updateTurn(sessionId, turnId, (t) => ({
              ...t,
              error: typeof error === 'string' ? error : (error as any).message || '未知错误',
              thinking: t.thinking ? { ...t.thinking, status: 'error' } : t.thinking,
            }))
            // rebuild 进行中：toast 友好提示，让用户去看进度
            if ((error as any)?.rebuildInProgress) {
              toast.error('向量库重建中，请等待完成后再提问')
            }
          },
          onCancelled: () => {
            // 用户主动 abort：保留已生成内容，turn 状态置 done（避免 spinner 残留）
            ctx.updateTurn(sessionId, turnId, (t) => {
              const partialAnswer = t.thinking?.partialAnswer ?? ''
              return {
                ...t,
                // 已有部分答案：保留并标记完成（不再 spinner）；无答案：标记已取消
                answer: partialAnswer || t.answer,
                error: partialAnswer ? undefined : '已取消',
                thinking: t.thinking
                  ? { ...t.thinking, status: partialAnswer ? 'done' : 'cancelled' }
                  : t.thinking,
              }
            })
            // 如果有部分答案，持久化（标记 done 让用户能复用）
            const currentTurn = ctx.activeSession?.turns.find((t) => t.id === turnId)
            if (currentTurn?.thinking?.partialAnswer) {
              ctx.persistTurn(sessionId, turnId, {
                answer: currentTurn.thinking.partialAnswer,
                sources: [],
                trace: [],
                usedProvider: undefined,
              })
            }
          },
        },
        ac.signal,
        session?.kb_scope ?? selectedKbForNewSession ?? undefined,
        sessionId,
        turnId,
        topK,
        mode,
      )
    } finally {
      setStreamingMap((prev) => {
        const next = { ...prev }
        delete next[sessionId]
        return next
      })
    }
  }

  function handleStop(sessionId?: string) {
    const sid = sessionId ?? ctx.activeId
    if (!sid) return
    abortRefs.current.get(sid)?.abort()
  }

  async function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault()
    const q = input.trim()
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
      sessionId = await ctx.createSession(selectedKbForNewSession || undefined, mode)
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
    void handleAsk(q, sessionId, history.length > 0 ? history : undefined, mode)
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
                <div className="flex items-center gap-1.5">
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
  const [sourcesExpanded, setSourcesExpanded] = useState(false)
  const thinkingStatus = turn.thinking?.status
  const isStreaming = thinkingStatus === 'streaming'
  const isSearchMode = turn.mode === 'basic' || turn.mode === 'deep'

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-start gap-2">
        <span className="mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded bg-secondary text-[10px] font-medium text-secondary-foreground">
          Q
        </span>
        <div className="flex-1 text-[13px] font-medium leading-relaxed">{turn.question}</div>
      </div>

      <div className="flex items-start gap-2">
        <span className="mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded bg-primary text-[10px] font-medium text-primary-foreground">
          A
        </span>
        <div className="flex-1">
          {isStreaming && turn.thinking ? (
            <ThinkingPanel thinking={turn.thinking} onStop={onStop} />
          ) : null}
          {!isStreaming && turn.error ? (
            <div className="flex items-center gap-2 text-destructive text-[12px]">
              <AlertCircle className="h-4 w-4" />
              {turn.error}
            </div>
          ) : null}
          {!isStreaming && !turn.error && isSearchMode ? (
            <SearchResultsList
              hits={(turn.sources ?? []) as SearchHit[]}
              question={turn.question}
            />
          ) : null}
          {!isStreaming && !turn.error && !isSearchMode && (
            <>
              <MarkdownContent content={turn.answer || ''} />
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

      {!isSearchMode && (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setSourcesExpanded(!sourcesExpanded)}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
          >
            <ChevronRight className={`h-3 w-3 transition-transform ${sourcesExpanded ? 'rotate-90' : ''}`} />
            引用来源 {turn.sources?.length || 0} 条
          </button>

          {sourcesExpanded && turn.sources && turn.sources.length > 0 ? (
            <div className="mt-2 space-y-1">
              {turn.sources.map((source: any, idx: number) => (
                <div
                  key={idx}
                  className="rounded-md bg-muted/20 p-2 text-[11px] text-muted-foreground"
                >
                  <div className="font-medium text-accent-foreground">
                    [{idx + 1}] {source.title || '未知来源'}
                  </div>
                  {source.metadata ? (
                    <div className="mt-0.5">
                      文件：{source.metadata.file_name || '未知'}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </Card>
  )
}
