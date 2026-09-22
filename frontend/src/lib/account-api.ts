export type Account = { roles: { id: string; name: string; enabled: boolean }[]; permissions: string[]; is_super: boolean; permission_revision: number; display_name?: string; note?: string; last_login?: number; created_at?: number; password_reset_at?: number;  id: string; username: string; role: 'admin' | 'member'; disabled: boolean; must_change_password: boolean; default_provider: string | null; csrf?: string }
export type Provider = { id: string; name: string; scope: 'personal' | 'team'; protocol: string; base_url: string; chat_model: string; enabled: boolean; has_api_key: boolean; manageable: boolean; usable: boolean; grantable: boolean }
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

export async function accountRequest<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await apiFetch(`/api/v1${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!res.ok) {
    const value = await res.json().catch(() => ({}))
    throw new Error(typeof value.detail === 'string' ? value.detail : `请求失败 (${res.status})`)
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
  providers: () => accountRequest<{ items: Provider[]; default_provider: string | null }>('/providers'),
  choose: (provider_id: string | null) => accountRequest('/account/provider', 'PUT', { provider_id }),
  members: () => accountRequest<Member[]>('/members'),
  users: () => accountRequest<Account[]>('/admin/users'),
}
