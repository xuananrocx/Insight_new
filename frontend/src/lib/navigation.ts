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
