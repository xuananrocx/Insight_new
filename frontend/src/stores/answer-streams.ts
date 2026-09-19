/**
 * 问答流式注册表（跨组件）：
 * - 切换会话 / 离开聊天页不中断生成（token 持续写入对应会话的 query cache，切回即见）
 * - 停止按钮、切换 KB、删除会话时主动 abort
 * - 会话列表用 streaming 渲染"生成中"徽标
 */
import { create } from 'zustand'

// AbortController 不参与渲染，放模块级 Map（zustand 里只存可序列化状态）
const aborters = new Map<string, AbortController>()

type AnswerStreamsState = {
  /** sessionId -> 正在生成的 turnId */
  streaming: Record<string, string>
  start: (sessionId: string, turnId: string, ac: AbortController) => void
  end: (sessionId: string) => void
  abort: (sessionId: string) => void
}

export const useAnswerStreams = create<AnswerStreamsState>((set) => ({
  streaming: {},
  start: (sessionId, turnId, ac) => {
    aborters.get(sessionId)?.abort()  // 同会话若有旧流，兜底停掉
    aborters.set(sessionId, ac)
    set((s) => ({ streaming: { ...s.streaming, [sessionId]: turnId } }))
  },
  end: (sessionId) => {
    aborters.delete(sessionId)
    set((s) => {
      if (!(sessionId in s.streaming)) return s
      const next = { ...s.streaming }
      delete next[sessionId]
      return { streaming: next }
    })
  },
  abort: (sessionId) => {
    aborters.get(sessionId)?.abort()
  },
}))

export function abortAnswerStream(sessionId: string) {
  useAnswerStreams.getState().abort(sessionId)
}
