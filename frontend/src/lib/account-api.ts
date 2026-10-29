export type Account = { roles: { id: string; name: string; enabled: boolean }[]; permissions: string[]; is_super: boolean; permission_revision: number; display_name?: string; note?: string; last_login?: number; created_at?: number; password_reset_at?: number;  id: string; username: string; role: 'admin' | 'member'; disabled: boolean; must_change_password: boolean; default_provider: string | null; csrf?: string }
export type ApiModel = { original_model_id?: string; context_tokens?: number | null; output_tokens?: number | null; model_id: string; display_name: string; connection_status?: string | null; connection_message?: string | null; connection_checked_at?: number | null; tools_status?: string | null; tools_message?: string | null; tools_checked_at?: number | null }
export type ModelSelection = { provider_id: string | null; model_id: string | null; valid: boolean; reason?: string | null; provider_name?: string; model_label?: string; retry_count?: number }
export type Provider = { edit_version?: string; id: string; name: string; scope: 'personal' | 'team'; protocol: string; base_url: string; chat_model: string; enabled: boolean; has_api_key: boolean; manageable: boolean; usable: boolean; grantable: boolean; models: ApiModel[] }
export type Member = { id: string; username: string; role?: string }

let csrf = ''
let accountId = ''
let generation = new AbortController()
export function setAccountSession(account: Account) {
  csrf = account.csrf || ''
  accountId = account.id
}
export function accountKey(key: string) { return accountId ? `${key}:${accountId}` : key }
export function clearAccountRequests() { generation.abort(); generation = new AbortController(); csrf = ''; accountId = '' }

export async function apiFetch(input: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers)
  headers.set('X-Insight-Request', '1')
  if (csrf) headers.set('X-CSRF-Token', csrf)
  const signal = init.signal ? AbortSignal.any([init.signal, generation.signal]) : generation.signal
  const res = await fetch(input, { ...init, headers, signal, credentials: 'same-origin', cache: 'no-store' })
  if (res.status === 401 && accountId && !input.includes('/auth/login')) window.dispatchEvent(new Event('insight-session-expired'))
  return res
}

export function accountErrorMessage(value: unknown, status: number): string {
  const fallback = `请求失败 (${status})`
  if (!value || typeof value !== 'object' || !('detail' in value)) return fallback
  if (typeof value.detail === 'string') return value.detail
  if (!Array.isArray(value.detail)) return fallback
  const labels: Record<string, string> = {
    username: '用户名', password: '密码', token: '初始化令牌',
    old_password: '当前密码', new_password: '新密码',
  }
  const messages = value.detail.flatMap((error: unknown) => {
    if (!error || typeof error !== 'object' || !('loc' in error) || !Array.isArray(error.loc)) return []
    const field = error.loc.at(-1)
    const label = typeof field === 'string' ? labels[field] || '输入项' : '输入项'
    const type = 'type' in error ? error.type : undefined
    const ctx = 'ctx' in error && error.ctx && typeof error.ctx === 'object' ? error.ctx : {}
    if (type === 'missing') return [`${label}不能为空`]
    if (type === 'string_pattern_mismatch' && field === 'username') {
      return ['用户名只能包含英文字母、数字、下划线、点、@ 和短横线']
    }
    if (type === 'string_too_short' && 'min_length' in ctx && typeof ctx.min_length === 'number') {
      return [`${label}至少需要 ${ctx.min_length} 个字符`]
    }
    if (type === 'string_too_long' && 'max_length' in ctx && typeof ctx.max_length === 'number') {
      return [`${label}不能超过 ${ctx.max_length} 个字符${field === 'token' ? '，请只复制 setup-token.txt 文件内的令牌内容' : ''}`]
    }
    // Validation responses may contain raw passwords/tokens in input or ctx.
    // Never stringify the error or display those values in the UI.
    return [`${label}格式不正确，请检查后重试`]
  })
  return [...new Set(messages)].join('；') || fallback
}

export async function accountRequest<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await apiFetch(`/api/v1${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!res.ok) {
    const value = await res.json().catch(() => ({}))
    throw new Error(accountErrorMessage(value, res.status))
  }
  return res.status === 204 ? undefined as T : res.json()
}

export const accountApi = {
  status: () => accountRequest<{ setup_required: boolean }>('/auth/status'),
  me: () => accountRequest<Account>('/auth/me'),
  login: (username: string, password: string) => accountRequest<Account>('/auth/login', 'POST', { username, password }),
  setup: (username: string, password: string, token: string) => accountRequest('/auth/setup', 'POST', { username, password, token }),
  logout: () => accountRequest('/auth/logout', 'POST'),
  password: (old_password: string, new_password: string) => accountRequest('/auth/password', 'POST', { old_password, new_password }),
  providers: () => accountRequest<{ items: Provider[]; default_provider: string | null; selection: ModelSelection }>('/providers'),
  choose: (provider_id: string | null, model_id?: string) => accountRequest<ModelSelection>('/account/provider', 'PUT', { provider_id, model_id }),
  apiPreferences: () => accountRequest<ModelSelection>('/account/api-preferences'),
  retryPreference: (retry_count: number) => accountRequest<ModelSelection>('/account/api-preferences', 'PUT', { retry_count }),
  sessionModel: (id: string) => accountRequest<ModelSelection>(`/sessions/${encodeURIComponent(id)}/model-selection`),
  selectSessionModel: (id: string, provider_id: string, model_id: string) => accountRequest<ModelSelection>(`/sessions/${encodeURIComponent(id)}/model-selection`, 'PUT', { provider_id, model_id }),
  members: () => accountRequest<Member[]>('/members'),
  users: () => accountRequest<Account[]>('/admin/users'),
}
