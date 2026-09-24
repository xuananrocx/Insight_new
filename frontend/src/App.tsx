import { ConfirmationProvider } from '@/components/confirmation-provider'
import { useBackground } from '@/hooks/use-background'
import { useState, type ReactNode } from 'react'
import { AuthBoundary, useAuth } from '@/hooks/use-auth'
import { AccountPage } from '@/pages/account-page'
import { UsersPage, RolesPage, ResourcesPage, TeamApiPage, AuditPage } from '@/pages/admin-pages'
import { adminLinks, pagePermission } from '@/lib/navigation'
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
import { AiLogsPage } from '@/pages/ai-logs-page'
import { LogsPage } from '@/pages/logs-page'
import { useLocalStorage } from '@/hooks/use-local-storage'
import { ReviewPage } from '@/pages/review-page'
import { SettingsPage } from '@/pages/settings-page'
import { StatsPage } from '@/pages/stats-page'

function MobileNav() {
  const { can } = useAuth()
  const [showLogs] = useLocalStorage<boolean>('amd-ui-show-logs', false)
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
                { to: '/settings', label: '个人设置' },
                ...(showLogs ? [{ to: '/logs', label: '系统日志' }] : []),
                ...adminLinks.filter(item => !item.permission || can(item.permission)),
              ].filter(item => !pagePermission[item.to] || can(pagePermission[item.to])).map((item) => (
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

function Guard({ permission, children }: { permission: string; children: ReactNode }) {
  const { can } = useAuth()
  return can(permission) ? children : <p className="p-8 text-muted-foreground">没有此页面的访问权限，请联系管理员。</p>
}
function Workspace() {
  useBackground()
  return (
    <ChatSessionProvider>
      <div className="flex h-screen overflow-hidden bg-background">
        <AppSidebar />
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <div className="flex items-center gap-2 border-b border-border px-2 md:hidden">
            <MobileNav />
            <span className="py-2 text-sm font-semibold">AMD AI</span>
          </div>
          <Topbar />
          <main data-workspace-content className="flex-1 overflow-y-auto">
            <Routes>
              <Route path="/" element={<ChatPage />} />
              <Route path="/knowledge" element={<Guard permission="documents.view"><KnowledgePage /></Guard>} />
              <Route path="/kbs" element={<KbPage />} />
              <Route path="/kbs/:id" element={<KbDetailPage />} />
              <Route path="/review" element={<Guard permission="feedback.view"><ReviewPage /></Guard>} />
              <Route path="/stats" element={<Guard permission="analysis.view"><StatsPage /></Guard>} />
              <Route path="/settings" element={<AccountPage />} />
              <Route path="/accounts" element={<Guard permission="users.view"><UsersPage /></Guard>} />
              <Route path="/admin/users" element={<Guard permission="users.view"><UsersPage /></Guard>} />
              <Route path="/admin/roles" element={<Guard permission="roles.view"><RolesPage /></Guard>} />
              <Route path="/admin/knowledge-access" element={<ResourcesPage />} />
              <Route path="/admin/team-api" element={<Guard permission="api.view"><TeamApiPage /></Guard>} />
              <Route path="/admin/audit" element={<Guard permission="audit.view"><AuditPage /></Guard>} />
              <Route path="/system-settings" element={<Guard permission="system.view"><SettingsPage /></Guard>} />
              <Route path="/ai-logs" element={<Guard permission="ai_logs.view"><AiLogsPage /></Guard>} />
              <Route path="/logs" element={<Guard permission="logs.view"><LogsPage /></Guard>} />
              <Route path="*" element={<p className="p-8">页面不存在。</p>} />
            </Routes>
          </main>
        </div>
      </div>
      <UploadActivityLayer />
      <Toaster position="top-right" richColors closeButton expand visibleToasts={10} />
    </ChatSessionProvider>
  )
}

export default function App() { return <AuthBoundary><ConfirmationProvider><Workspace /></ConfirmationProvider></AuthBoundary> }
