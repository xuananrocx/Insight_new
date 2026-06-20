import { createContext, useContext } from 'react'

import { useChatSessions } from './use-chat-sessions'

type ChatSessionsCtx = ReturnType<typeof useChatSessions>

const Ctx = createContext<ChatSessionsCtx | null>(null)

export function ChatSessionProvider({ children }: { children: React.ReactNode }) {
  const value = useChatSessions()
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useChatSessionsCtx(): ChatSessionsCtx {
  const ctx = useContext(Ctx)
  if (!ctx) {
    throw new Error('useChatSessionsCtx must be used within ChatSessionProvider')
  }
  return ctx
}
