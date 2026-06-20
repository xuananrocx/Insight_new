import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, type PersistedTurn, type QaSource, type QaTraceStage, type SessionDetail, type SessionSummary } from '@/lib/api'

export type ThinkingState = {
  stages: QaTraceStage[]
  partialAnswer: string
  status: 'streaming' | 'done' | 'stopped' | 'error' | 'cancelled'
  startedAt: number
  elapsedMs?: number
  warmup?: string
  sources?: QaSource[]
  usedProvider?: string
}

export type ChatTurn = {
  id: string
  question: string
  answer?: string
  sources?: unknown[]
  trace?: QaTraceStage[]
  loading?: boolean
  error?: string
  liked?: boolean
  feedbackSaved?: boolean
  thinking?: ThinkingState
}

export type ChatSession = Omit<SessionSummary, 'turn_count'> & {
  turns: ChatTurn[]
}

const ACTIVE_KEY = 'amd-ui-chat-active'

function genId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function persistedToChatTurn(t: PersistedTurn): ChatTurn {
  // cache 里可能存了 thinking（流式期间塞进去的），转回 ChatTurn 时带上
  const thinking = (t as PersistedTurn & { thinking?: ThinkingState }).thinking
  return {
    id: t.id,
    question: t.question,
    answer: t.answer ?? undefined,
    sources: t.sources as unknown as QaSource[],
    trace: t.trace,
    liked: t.liked,
    error: t.error ?? undefined,
    thinking,
  }
}

function chatTurnToAddRequest(t: ChatTurn, createdAt: number) {
  return {
    id: t.id,
    question: t.question,
    answer: t.answer,
    sources: (t.sources ?? []) as QaSource[],
    trace: (t.trace ?? []) as QaTraceStage[],
    used_provider: t.thinking?.usedProvider,
    liked: t.liked ?? false,
    error: t.error,
    created_at: createdAt,
  }
}

export function useChatSessions() {
  const qc = useQueryClient()
  const [activeId, setActiveId] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null
    return localStorage.getItem(ACTIVE_KEY) || null
  })
  // 本地 turns state，封装 sessionId 防止跨会话污染
  const [turnsState, setTurnsState] = useState<{ sessionId: string; turns: ChatTurn[] } | null>(null)

  // 列表（不含 turns，节省带宽）
  const listQuery = useQuery({
    queryKey: ['sessions'],
    queryFn: api.sessions.list,
    refetchOnWindowFocus: false,
  })

  // KB 列表（用于获取默认 KB）
  const kbListQuery = useQuery({
    queryKey: ['kbs'],
    queryFn: api.kb.list,
    refetchOnWindowFocus: false,
  })

  // 当前会话详情（含 turns）
  const detailQuery = useQuery({
    queryKey: ['session', activeId],
    queryFn: () => api.sessions.get(activeId!),
    enabled: !!activeId,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  // activeId 持久化到 localStorage
  useEffect(() => {
    if (activeId) localStorage.setItem(ACTIVE_KEY, activeId)
    else localStorage.removeItem(ACTIVE_KEY)
  }, [activeId])

  // activeSession 计算时严格检查 detailQuery.data.id 匹配（避免切换瞬间显示上一个会话数据）
  // turns 优先用 turnsState.turns（含流式 thinking），但 turnsState.sessionId 不匹配时
  // fallback 到 detailQuery.data.turns（持久化数据）—— 这样切走再切回时仍能看到完整 answer
  const activeSession: ChatSession | null = (() => {
    if (!activeId || !detailQuery.data) return null
    if (detailQuery.data.id !== activeId) return null
    const d = detailQuery.data
    const turns = turnsState && turnsState.sessionId === activeId
      ? turnsState.turns
      : (d.turns ?? []).map(persistedToChatTurn)
    return {
      id: d.id,
      title: d.title,
      created_at: d.created_at,
      updated_at: d.updated_at,
      turn_count: turns.length,
      kb_scope: d.kb_scope ?? null,
      turns,
    }
  })()

  // 用 ref 让回调里能读到最新的 turns（避免 stale closure）
  const turnsRef = useRef<ChatTurn[] | null>(null)
  useEffect(() => {
    turnsRef.current = turnsState?.turns ?? null
  }, [turnsState])

  // 跟踪 turns 已经为哪个 session 初始化过，避免 effect 覆盖用户乐观更新
  const initializedFor = useRef<string | null>(null)
  useEffect(() => {
    if (!activeId) {
      initializedFor.current = null
      return
    }
    if (initializedFor.current !== activeId) {
      if (detailQuery.data && detailQuery.data.id === activeId) {
        initializedFor.current = activeId
        const fresh = (detailQuery.data.turns ?? []).map(persistedToChatTurn)
        turnsRef.current = fresh
        setTurnsState({ sessionId: activeId, turns: fresh })
      }
      // detailQuery 还在加载时不动 turnsState：activeSession 的 sessionId 检查会兜底返回 null
    }
  }, [activeId, detailQuery.data])

  // ===== Mutations =====

  const createMutation = useMutation({
    mutationFn: api.sessions.create,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  })

  const deleteMutation = useMutation({
    mutationFn: api.sessions.remove,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
    },
  })

  const renameMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api.sessions.update(id, { title }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  })

  const addTurnMutation = useMutation({
    mutationFn: ({ sessionId, payload }: { sessionId: string; payload: ReturnType<typeof chatTurnToAddRequest> }) =>
      api.sessions.addTurn(sessionId, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  })

  const patchTurnMutation = useMutation({
    mutationFn: ({ sessionId, turnId, payload }: {
      sessionId: string
      turnId: string
      payload: Parameters<typeof api.sessions.patchTurn>[2]
    }) => api.sessions.patchTurn(sessionId, turnId, payload),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      qc.invalidateQueries({ queryKey: ['session', vars.sessionId] })
    },
  })

  // ===== 对外暴露的方法（保持原有签名兼容）=====

  const createSession = useCallback(async (kbId?: string): Promise<string> => {
    const id = genId('s')
    const now = Date.now()

    // 获取 KB：优先使用传入的 kbId，否则使用默认 KB
    let kb_scope = kbId
    if (!kb_scope) {
      const defaultKb = kbListQuery.data?.find(kb => kb.is_default)
      kb_scope = defaultKb?.id || undefined
    }

    await createMutation.mutateAsync({ id, title: '新会话', created_at: now, kb_scope })
    // 乐观：在 detail cache 里塞一个空 session，避免 appendTurn 时 activeSession 为空
    const optimistic: SessionDetail = {
      id,
      title: '新会话',
      created_at: now,
      updated_at: now,
      turn_count: 0,
      kb_scope,
      turns: [],
    }
    qc.setQueryData(['session', id], optimistic)
    setActiveId(id)
    return id
  }, [createMutation, qc, kbListQuery.data])

  // 切换会话时 abort 旧 SSE（流式中切会话，旧流应停止推送 token）
  // 由 chat-page 通过 onBeforeSelect 注册回调
  const beforeSelectRef = useRef<((newId: string) => void) | null>(null)
  const registerBeforeSelect = useCallback((fn: (newId: string) => void) => {
    beforeSelectRef.current = fn
  }, [])
  const selectSessionWithAbort = useCallback((id: string) => {
    try {
      beforeSelectRef.current?.(id)
    } catch (e) {
      console.warn('beforeSelect failed', e)
    }
    setActiveId(id)
  }, [])

  const clearActive = useCallback(() => setActiveId(null), [])

  const deleteSession = useCallback(
    (id: string) => {
      deleteMutation.mutate(id, {
        onSuccess: () => {
          if (id === activeId) {
            // 切到列表里下一个；列表数据可能还没刷新，先用当前数据
            const remaining = (listQuery.data ?? []).filter((s) => s.id !== id)
            setActiveId(remaining[0]?.id ?? null)
          }
        },
      })
    },
    [deleteMutation, activeId, listQuery.data],
  )

  const renameSession = useCallback(
    (id: string, title: string) => {
      renameMutation.mutate({ id, title })
    },
    [renameMutation],
  )

  // 通用：直接改本地 turns state（流式期间高频调用，不触发 API）
  // 如果 sessionId 跟当前 turnsState.sessionId 不一致（用户已切到别的会话），
  // 不修改 turnsState（避免污染当前会话），cache 更新由调用方负责
  const _updateTurns = useCallback(
    (sessionId: string, updater: (turns: ChatTurn[]) => ChatTurn[]) => {
      setTurnsState((prev) => {
        if (prev && prev.sessionId !== sessionId) return prev  // 切走了，不动
        const base = prev ? prev.turns : []
        const next = updater(base)
        turnsRef.current = next
        return { sessionId, turns: next }
      })
    },
    [],
  )

  const updateSession = useCallback(
    (id: string, updater: (s: ChatSession) => ChatSession) => {
      _updateTurns(id, (turns) => {
        const fake: ChatSession = {
          id,
          title: detailQuery.data?.title ?? '',
          created_at: detailQuery.data?.created_at ?? 0,
          updated_at: detailQuery.data?.updated_at ?? 0,
          kb_scope: detailQuery.data?.kb_scope ?? null,
          turns,
        }
        return updater(fake).turns
      })
    },
    [detailQuery.data, _updateTurns],
  )

  // 更新会话的 kb_scope（同时清空后端旧 turns，避免旧 KB 的回答污染新 KB）
  const updateSessionKb = useCallback(
    async (id: string, kbScope: string | null) => {
      await api.sessions.update(id, { kb_scope: kbScope ?? undefined })
      // 同步清空后端 turns（旧 KB 的回答对新 KB 没意义）
      try {
        await api.sessions.clearAllTurns(id)
      } catch (e) {
        console.warn('clearAllTurns failed:', e)
      }
      // 同步清空前端状态
      setTurnsState({ sessionId: id, turns: [] })
      // P1-14: removeQueries 比 invalidate 更彻底（清空 cache，强制 detailQuery refetch
      // 时 initializedFor.current 重置，避免 stale turnsState）
      qc.removeQueries({ queryKey: ['session', id] })
      qc.invalidateQueries({ queryKey: ['sessions'] })
    },
    [qc],
  )

  // 清空当前会话的 turns
  const clearTurns = useCallback(() => {
    if (activeId) {
      setTurnsState({ sessionId: activeId, turns: [] })
    }
  }, [activeId])

  const appendTurn = useCallback(
    (id: string, turn: ChatTurn) => {
      _updateTurns(id, (turns) => [...turns, turn])
      // 同步更新 detail cache，确保流式中切走再切回时 fallback 到 cache 能拿到 turn
      // 否则 detail cache 里 turns=[] → activeSession.turns=[] → 显示空状态 UI
      qc.setQueryData<SessionDetail>(['session', id], (old) => {
        if (!old) return old
        return {
          ...old,
          updated_at: Date.now(),
          turn_count: old.turn_count + 1,
          turns: [...old.turns, {
            id: turn.id,
            question: turn.question,
            answer: turn.answer ?? null,
            sources: (turn.sources ?? []) as QaSource[],
            trace: turn.trace ?? [],
            used_provider: turn.thinking?.usedProvider ?? null,
            liked: turn.liked ?? false,
            error: turn.error ?? null,
            created_at: Date.now(),
            thinking: turn.thinking,  // 塞进 cache，让切走再切回时也能拿到 thinking
          } as PersistedTurn & { thinking?: ThinkingState }],
        }
      })
      // 异步 POST 到 API（fire-and-forget）
      addTurnMutation.mutate(
        { sessionId: id, payload: chatTurnToAddRequest(turn, Date.now()) },
        { onError: (e) => console.error('appendTurn POST failed', e) },
      )
    },
    [_updateTurns, addTurnMutation],
  )

  const updateTurn = useCallback(
    (id: string, turnId: string, updater: (t: ChatTurn) => ChatTurn) => {
      _updateTurns(id, (turns) => turns.map((t) => (t.id === turnId ? updater(t) : t)))
      // 同步更新 detail cache（把 thinking 也塞进去，绕过 PersistedTurn 类型限制）
      // 这样切走期间 token 也能累积到 cache，切回时从 cache 拿到完整 thinking
      qc.setQueryData<SessionDetail>(['session', id], (old) => {
        if (!old) return old
        const targetIdx = old.turns.findIndex((t) => t.id === turnId)
        if (targetIdx < 0) return old
        const persisted = old.turns[targetIdx]
        // 把 cache 里可能存在的旧 thinking 一起合并
        const prevThinking = (persisted as PersistedTurn & { thinking?: ThinkingState }).thinking
        const prevChatTurn: ChatTurn = {
          id: persisted.id,
          question: persisted.question,
          answer: persisted.answer ?? undefined,
          sources: persisted.sources as unknown as QaSource[],
          trace: persisted.trace,
          liked: persisted.liked,
          error: persisted.error ?? undefined,
          thinking: prevThinking,
        }
        const updated = updater(prevChatTurn)
        const newTurn = {
          ...persisted,
          answer: updated.answer ?? null,
          sources: (updated.sources ?? []) as QaSource[],
          trace: updated.trace ?? [],
          used_provider: updated.thinking?.usedProvider ?? persisted.used_provider ?? null,
          error: updated.error ?? null,
          thinking: updated.thinking,
        } as PersistedTurn & { thinking?: ThinkingState }
        const newTurns = [...old.turns]
        newTurns[targetIdx] = newTurn
        return { ...old, turns: newTurns }
      })
    },
    [_updateTurns, qc],
  )

  // 把当前 turn 持久化到 API（流式结束/中断时调用）
  // override 显式传入 answer/sources/trace/error 等，避免依赖异步的 turnsRef 同步
  const persistTurn = useCallback(
    async (
      id: string,
      turnId: string,
      override?: {
        answer?: string
        sources?: QaSource[]
        trace?: QaTraceStage[]
        error?: string
        usedProvider?: string
      },
    ) => {
      const cur = turnsRef.current
      const baseTurn = cur?.find((t) => t.id === turnId)
      const answer = override?.answer ?? baseTurn?.answer
      const sources = (override?.sources ?? (baseTurn?.sources as QaSource[])) ?? []
      const trace = (override?.trace ?? (baseTurn?.trace as QaTraceStage[])) ?? []
      const usedProvider = override?.usedProvider ?? baseTurn?.thinking?.usedProvider
      const error = override?.error ?? baseTurn?.error
      const liked = baseTurn?.liked ?? false
      if (!answer && !error) return  // 没东西可持久化

      try {
        await patchTurnMutation.mutateAsync({
          sessionId: id,
          turnId,
          payload: {
            answer,
            sources,
            trace,
            used_provider: usedProvider,
            liked,
            error,
            updated_at: Date.now(),
          },
        })
        // PATCH 成功后立即同步更新 detail cache
        qc.setQueryData<SessionDetail>(['session', id], (old) => {
          if (!old) return old
          return {
            ...old,
            updated_at: Date.now(),
            turns: old.turns.map((t) =>
              t.id === turnId
                ? {
                    ...t,
                    answer: answer ?? null,
                    sources,
                    trace,
                    used_provider: usedProvider ?? null,
                    liked,
                    error: error ?? null,
                  }
                : t
            ),
          }
        })
      } catch (e) {
        console.error('persistTurn failed', e)
      }
    },
    [patchTurnMutation, qc],
  )

  return {
    sessions: listQuery.data ?? [],
    activeSession,
    activeId,
    createSession,
    selectSession: selectSessionWithAbort,
    registerBeforeSelect,
    clearActive,
    deleteSession,
    renameSession,
    updateSession,
    updateSessionKb,
    clearTurns,
    appendTurn,
    updateTurn,
    persistTurn,
    isLoadingList: listQuery.isLoading,
    isLoadingDetail: detailQuery.isLoading,
  }
}
