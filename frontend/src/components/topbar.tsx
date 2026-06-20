import { Plus } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { ThemeToggle } from '@/components/theme-toggle'
import { useChatSessionsCtx } from '@/hooks/chat-session-context'

export function Topbar() {
  const ctx = useChatSessionsCtx()
  const navigate = useNavigate()

  return (
    <header className="flex h-12 items-center justify-between border-b bg-background px-4">
      <div className="flex items-center gap-2">
        <span className="text-[12px] text-muted-foreground">Workspace</span>
        <span className="text-[12px] text-muted-foreground">/</span>
        <span className="text-[13px] font-medium">Insight</span>
        <span className="ml-2 inline-flex h-1.5 w-1.5 rounded-full bg-success" title="服务正常" />
      </div>

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          className="gap-1.5 text-[12px]"
          onClick={() => {
            ctx.clearActive()
            navigate('/')
          }}
          title="开始新对话"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>新提问</span>
        </Button>
        <ThemeToggle />
      </div>
    </header>
  )
}
