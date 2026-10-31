import { ConfirmationProvider } from '@/components/confirmation-provider'
import { useGlassOpacity } from '@/hooks/use-glass-opacity'
import { useBackground } from '@/hooks/use-background'
import { type ReactNode } from 'react'
import { AuthBoundary, useAuth } from '@/hooks/use-auth'
import { AccountPage } from '@/pages/account-page'
import { UsersPage, RolesPage, ResourcesPage, TeamApiPage, AuditPage } from '@/pages/admin-pages'
import { Routes, Route } from 'react-router-dom'
import { Toaster } from 'sonner'

import { AppSidebar } from '@/components/app-sidebar'
import { UploadActivityLayer } from '@/components/upload-activity-layer'
import { ChatSessionProvider } from '@/hooks/chat-session-context'
import { ChatPage } from '@/pages/chat-page'
import KbDetailPage from '@/pages/kb-detail-page'
import { KnowledgePage } from '@/pages/knowledge-page'
import KbPage from '@/pages/kb-page'
import { AiLogsPage } from '@/pages/ai-logs-page'
import { LogsPage } from '@/pages/logs-page'
import { ReviewPage } from '@/pages/review-page'
import { SettingsPage } from '@/pages/settings-page'
import { StatsPage } from '@/pages/stats-page'

function Guard({ permission, children }: { permission: string; children: ReactNode }) {
  const { can } = useAuth()
  return can(permission) ? children : <p className="p-8 text-muted-foreground">没有此页面的访问权限，请联系管理员。</p>
}
function Workspace() {
  useBackground()
  useGlassOpacity()
  return (
    <ChatSessionProvider>
      <div className="workspace-shell flex h-dvh flex-col overflow-hidden bg-background md:flex-row">
        <AppSidebar />
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <main data-workspace-content className="min-h-0 flex-1 overflow-y-auto">
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
