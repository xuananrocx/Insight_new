import { useState } from 'react'
import { Routes, Route, useNavigate } from 'react-router-dom'
import { Toaster } from 'sonner'
import { Menu } from 'lucide-react'

import { AppSidebar } from '@/components/app-sidebar'
import { Topbar } from '@/components/topbar'
import { UploadActivityLayer } from '@/components/upload-activity-layer'
import { Button } from '@/components/ui/button'
import { ChatSessionProvider } from '@/hooks/chat-session-context'
import { ChatPage } from '@/pages/chat-page'
import KbDetailPage from '@/pages/kb-detail-page'
import { KnowledgePage } from '@/pages/knowledge-page'
import KbPage from '@/pages/kb-page'
import { LogsPage } from '@/pages/logs-page'
import { AiLogsPage } from '@/pages/ai-logs-page'
import { ReviewPage } from '@/pages/review-page'
import { SettingsPage } from '@/pages/settings-page'
import { StatsPage } from '@/pages/stats-page'

function MobileNav() {
  // 移动端下拉菜单（< md 屏幕）
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  return (
    <div className="md:hidden">
      <Button
        variant="ghost"
        size="sm"
        className="px-2"
        onClick={() => setOpen((v) => !v)}
        aria-label="导航"
      >
        <Menu className="h-5 w-5" />
      </Button>
      {open && (
        <>
          <div className="fixed inset-0 z-40 bg-black/30" onClick={() => setOpen(false)} />
          <div className="fixed left-0 top-0 z-50 h-full w-[240px] bg-background shadow-xl">
            <div className="border-b border-border p-3 text-sm font-medium">导航</div>
            <ul className="p-2 text-sm">
              {[
                { to: '/', label: '对话' },
                { to: '/knowledge', label: '知识' },
                { to: '/kbs', label: '知识库' },
                { to: '/settings', label: '设置' },
              ].map((item) => (
                <li key={item.to}>
                  <button
                    className="w-full rounded px-3 py-2 text-left hover:bg-muted"
                    onClick={() => {
                      navigate(item.to)
                      setOpen(false)
                    }}
                  >
                    {item.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  )
}

function App() {
  return (
    <ChatSessionProvider>
      <div className="flex h-screen overflow-hidden bg-background">
        <AppSidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          <div className="flex items-center gap-2 border-b border-border px-2 md:hidden">
            <MobileNav />
            <span className="py-2 text-sm font-semibold">AMD AI</span>
          </div>
          <Topbar />
          <main className="flex-1 overflow-y-auto">
            <Routes>
              <Route path="/" element={<ChatPage />} />
              <Route path="/knowledge" element={<KnowledgePage />} />
              <Route path="/kbs" element={<KbPage />} />
              <Route path="/kbs/:id" element={<KbDetailPage />} />
              <Route path="/review" element={<ReviewPage />} />
              <Route path="/stats" element={<StatsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/ai-logs" element={<AiLogsPage />} />
              <Route path="/logs" element={<LogsPage />} />
            </Routes>
          </main>
        </div>
      </div>
      <UploadActivityLayer />
      <Toaster position="top-right" richColors closeButton expand visibleToasts={10} />
    </ChatSessionProvider>
  )
}

export default App
