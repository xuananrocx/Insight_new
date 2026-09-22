import { createContext, useContext, useEffect, useState, type ReactNode, type FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { InsightLogo } from '@/components/insight-logo'
import { accountApi, clearAccountRequests, setAccountSession, type Account } from '@/lib/account-api'

const AuthContext = createContext<{ user: Account; can: (permission: string) => boolean; logout: () => Promise<void> } | null>(null)
export const accountInput = 'w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30'

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('Account context missing')
  return value
}

export function PasswordForm({ onDone }: { onDone: () => void }) {
  const [old, setOld] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(e: FormEvent) {
    e.preventDefault(); setError('')
    if (next !== repeat) { setError('两次新密码不一致'); return }
    setBusy(true)
    try { await accountApi.password(old, next); onDone() } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="space-y-3">
    <label className="block space-y-1 text-sm"><span>当前密码</span><input className={accountInput} type="password" autoComplete="current-password" value={old} onChange={e => setOld(e.target.value)} required maxLength={128} /></label>
    <label className="block space-y-1 text-sm"><span>新密码（至少 12 个字符）</span><input className={accountInput} type="password" autoComplete="new-password" value={next} onChange={e => setNext(e.target.value)} required minLength={12} maxLength={128} /></label>
    <label className="block space-y-1 text-sm"><span>确认新密码</span><input className={accountInput} type="password" autoComplete="new-password" value={repeat} onChange={e => setRepeat(e.target.value)} required minLength={12} maxLength={128} /></label>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <Button disabled={busy} type="submit">{busy ? '正在保存…' : '修改密码并重新登录'}</Button>
  </form>
}

export function AuthBoundary({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [user, setUser] = useState<Account | null>(null)
  const [loading, setLoading] = useState(true)
  const [setup, setSetup] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  function resetPage() {
    clearAccountRequests(); qc.clear()
    window.location.replace('/')
  }
  async function logout() {
    await accountApi.logout().catch(() => {})
    localStorage.setItem('insight-auth-change', String(Date.now()))
    resetPage()
  }
  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const s = await accountApi.status()
        if (!alive) return
        setSetup(s.setup_required)
        if (!s.setup_required) {
          const me = await accountApi.me().catch(() => null)
          if (alive && me) { setAccountSession(me); setUser(me) }
        }
      } catch (err) { if (alive) setError((err as Error).message) }
      finally { if (alive) setLoading(false) }
    }
    void load()
    return () => { alive = false }
  }, [])
  useEffect(() => {
    const expire = () => { clearAccountRequests(); qc.clear(); setUser(null); setError('登录已失效，请重新登录'); window.location.replace('/') }
    const storage = (e: StorageEvent) => { if (e.key === 'insight-auth-change') expire() }
    window.addEventListener('insight-session-expired', expire)
    window.addEventListener('storage', storage)
    return () => { window.removeEventListener('insight-session-expired', expire); window.removeEventListener('storage', storage) }
  }, [qc])

  useEffect(() => {
    if (!user) return
    let alive = true
    let running = false
    const refresh = async () => {
      if (running) return
      running = true
      try {
        const me = await accountApi.me()
        if (!alive) return
        if (me.permission_revision !== user.permission_revision) {
          clearAccountRequests(); setAccountSession(me)
          await qc.cancelQueries(); qc.clear(); setUser(me)
        }
      } catch { /* Authentication expiry is handled by apiFetch. */ }
      finally { running = false }
    }
    const timer = window.setInterval(refresh, 10000)
    window.addEventListener('focus', refresh)
    window.addEventListener('insight-permissions-changed', refresh)
    return () => { alive = false; clearInterval(timer); window.removeEventListener('focus', refresh); window.removeEventListener('insight-permissions-changed', refresh) }
  }, [user, qc])

  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('')
    try {
      if (setup) { await accountApi.setup(username, password, token); setSetup(false); setToken('') }
      const account = await accountApi.login(username, password)
      qc.clear(); setAccountSession(account); setUser(account); setPassword('')
      localStorage.setItem('insight-auth-change', String(Date.now()))
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  if (loading) return <div className="grid min-h-screen place-items-center text-muted-foreground">正在连接 Insight…</div>
  if (user && !user.must_change_password) return <AuthContext.Provider value={{ user, logout, can: permission => user.permissions.includes(permission) }}>{children}</AuthContext.Provider>
  return <div className="grid min-h-screen place-items-center bg-muted/30 p-6">
    <div className="w-full max-w-md rounded-xl border bg-background p-8 shadow-sm">
      <div className="mb-6">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary" aria-hidden="true"><InsightLogo size="md" variant="solid" /></div>
          <p className="text-sm font-semibold text-primary">Insight · 知识库</p>
        </div>
        <h1 className="mt-4 text-2xl font-semibold">{user ? '设置你的新密码' : setup ? '初始化管理员账号' : '登录工作空间'}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{user ? (user.password_reset_at ? '管理员已重置密码，请设置自己的新密码。' : '首次登录需要修改管理员提供的初始密码。') : setup ? '首次初始化将现有知识库、会话和 API 配置归入该账号。' : '账号由团队管理员创建，暂不开放注册。'}</p>
      </div>
      {user ? <><PasswordForm onDone={resetPage} /><Button className="mt-3" variant="ghost" onClick={logout}>退出登录</Button></> : <form className="space-y-4" onSubmit={submit}>
        {setup && <label className="block space-y-1 text-sm"><span>服务器初始化令牌</span><input className={accountInput} type="password" value={token} onChange={e => setToken(e.target.value)} required autoComplete="off" /><span className="block text-xs text-muted-foreground">从服务器数据目录的 setup-token.txt 文件读取。</span></label>}
        <label className="block space-y-1 text-sm"><span>用户名</span><input className={accountInput} value={username} onChange={e => setUsername(e.target.value)} required autoComplete="username" minLength={2} maxLength={64} pattern="[a-zA-Z0-9_.@\-]+" /></label>
        <label className="block space-y-1 text-sm"><span>{setup ? '密码（至少 12 个字符）' : '密码'}</span><input className={accountInput} type="password" value={password} onChange={e => setPassword(e.target.value)} required autoComplete={setup ? 'new-password' : 'current-password'} minLength={setup ? 12 : 1} maxLength={128} /></label>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <Button className="w-full" disabled={busy} type="submit">{busy ? '正在处理…' : setup ? '初始化并登录' : '登录'}</Button>
      </form>}
    </div>
  </div>
}
