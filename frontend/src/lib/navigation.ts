export const pagePermission: Record<string, string> = {
  '/knowledge': 'documents.view', '/review': 'feedback.view', '/stats': 'analysis.view', '/ai-logs': 'ai_logs.view', '/logs': 'logs.view',
}
export const adminLinks = [
  { to: '/admin/users', label: '用户管理', permission: 'users.view' },
  { to: '/admin/roles', label: '角色管理', permission: 'roles.view' },
  { to: '/admin/knowledge-access', label: '知识库授权', permission: '' },
  { to: '/admin/team-api', label: '团队 API', permission: 'api.view' },
  { to: '/admin/audit', label: '操作日志', permission: 'audit.view' },
  { to: '/system-settings', label: '系统设置', permission: 'system.view' },
]

export const DEFAULT_MENU_ORDER = ['/', '/knowledge', '/kbs', '/review', '/stats', '/ai-logs', '/settings']

export const MENU_LABELS: Record<string, string> = {
  '/': '提问', '/knowledge': '文档管理', '/kbs': '知识库管理', '/review': '审批',
  '/stats': '分析', '/ai-logs': 'AI 日志', '/settings': '个人设置', '/logs': '系统日志',
}

// Normalize saved order without removing temporarily inaccessible menu positions.
export function normalizeMenuOrder(order: readonly string[]): string[] {
  return [...new Set([...order, ...DEFAULT_MENU_ORDER, '/logs'])]
    .filter(path => Object.hasOwn(MENU_LABELS, path))
}

export function visibleMenuOrder(order: readonly string[], can: (permission: string) => boolean, showLogs: boolean): string[] {
  return normalizeMenuOrder(order).filter(path =>
    (path !== '/logs' || showLogs) && (!pagePermission[path] || can(pagePermission[path])),
  )
}
