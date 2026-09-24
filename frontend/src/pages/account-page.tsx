import { PersonalPreferencesCard } from '@/components/personal-preferences-card'
import { DeepAiOptionsFields } from '@/components/deep-ai-options'
import { OptionSelect } from '@/components/ui/select'
import { useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ManagementDialog } from '@/components/management-dialog'
import { accountInput, PasswordForm, useAuth } from '@/hooks/use-auth'
import { useLocalStorage } from '@/hooks/use-local-storage'
import { useApiRetryCount } from '@/hooks/use-api-retry-count'
import { accountApi, accountRequest, type Provider } from '@/lib/account-api'

export { Toggle } from '@/components/ui/toggle-switch'
import { Toggle } from '@/components/ui/toggle-switch'
import { AccessEditor } from '@/components/access-editor'

export function Grants({ path, isKb = false }: { path: string; isKb?: boolean }) {
  const [open, setOpen] = useState(false)
  return <><Button size="sm" variant="outline" onClick={() => setOpen(true)}>{isKb ? '管理知识库授权' : '使用授权'}</Button>{open && <ManagementDialog title={isKb ? '知识库授权' : 'API 使用授权'} onClose={() => setOpen(false)}><AccessEditor kind={isKb ? 'kb' : 'api'} id={path.split('/').pop()!} /></ManagementDialog>}</>
}

function ProviderForm({ initial, scope, onClose }: { initial?: Provider; scope: 'personal' | 'team'; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(initial?.name || '')
  const [protocol, setProtocol] = useState(initial?.protocol || 'openai')
  const [url, setUrl] = useState(initial?.base_url || '')
  const [model, setModel] = useState(initial?.chat_model || '')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('')
    try {
      await accountRequest(`/providers${initial ? '/'+initial.id : ''}`, initial ? 'PUT' : 'POST', { name, scope, protocol, base_url: url, chat_model: model, api_key: key || (initial ? null : ''), enabled: initial?.enabled ?? true })
      await qc.invalidateQueries({ queryKey: ['account-providers'] }); onClose(); toast.success('API 配置已保存')
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  return <ManagementDialog title={`${initial ? '编辑' : '新增'}${scope === 'team' ? '团队公共' : '个人'} API`} busy={busy} onClose={onClose}><form onSubmit={save} className="space-y-4">
    <div className="grid gap-3 md:grid-cols-2"><label className="space-y-1 text-sm">名称<input className={accountInput} value={name} onChange={e => setName(e.target.value)} required maxLength={80} /></label><label className="space-y-1 text-sm">协议<OptionSelect aria-label="协议" className={accountInput} value={protocol} onValueChange={setProtocol} disabled={busy} options={[{ value: 'openai', label: 'OpenAI 兼容' }, { value: 'anthropic', label: 'Anthropic' }, { value: 'anthropic-arch', label: 'Anthropic Arch' }]} /></label></div>
    <label className="block space-y-1 text-sm">API 地址<input type="url" className={accountInput} value={url} onChange={e => setUrl(e.target.value)} placeholder="https://api.example.com/v1" required maxLength={2048} />{protocol === 'openai' && <span className="block text-xs text-muted-foreground">只填域名时自动使用 /v1；自定义路径请填写完整 API 路径，或 /chat/completions、/responses 完整端点。</span>}</label>
    <label className="block space-y-1 text-sm">模型名称<input className={accountInput} value={model} onChange={e => setModel(e.target.value)} placeholder="服务商提供的模型名称" required maxLength={200} /></label>
    <label className="block space-y-1 text-sm">API 密钥<input type="password" className={accountInput} autoComplete="new-password" value={key} onChange={e => setKey(e.target.value)} placeholder={initial?.has_api_key ? '已保存；留空保留原密钥' : '输入 API 密钥'} maxLength={8192} /></label>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}<div className="flex gap-2"><Button disabled={busy} type="submit">保存</Button><Button type="button" variant="outline" onClick={onClose}>取消</Button></div>
  </form></ManagementDialog>
}

export function ProviderSettings({ team = false }: { team?: boolean }) {
  const { can } = useAuth()
  const qc = useQueryClient()
  const providers = useQuery({ queryKey: ['account-providers'], queryFn: accountApi.providers })
  const [form, setForm] = useState<{ scope: 'personal' | 'team'; initial?: Provider } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  async function act(id: string, action: 'choose' | 'test' | 'test-tools' | 'delete' | 'toggle') {
    if (action === 'delete' && !window.confirm('删除此 API 配置？使用它的请求将无法继续。')) return
    setBusy(id)
    try {
      if (action === 'test-tools') {
        const result = await accountRequest<{ supported: boolean | null; message: string }>(`/providers/${id}/test-tools`, 'POST')
        if (result.supported) toast.success(result.message)
        else toast.error(result.message)
        return
      }
      if (action === 'choose') await accountApi.choose(id)
      else if (action === 'toggle') { const p = providers.data!.items.find(x => x.id === id)!; await accountRequest(`/providers/${id}`, 'PUT', { ...p, api_key: null, enabled: !p.enabled }) }
      else await accountRequest(`/providers/${id}${action === 'test' ? '/test' : ''}`, action === 'test' ? 'POST' : 'DELETE')
      await qc.invalidateQueries({ queryKey: ['account-providers'] }); await qc.invalidateQueries({ queryKey: ['llm-providers'] }); toast.success(action === 'test' ? '连接测试成功' : '已更新')
    } catch (e) { toast.error((e as Error).message) } finally { setBusy(null) }
  }
  return <Card className="space-y-4 p-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">{team ? '团队 API' : '我的 AI API'}</h2><div className="flex gap-2">{!team && can('api.personal') && <Button size="sm" onClick={() => setForm({ scope: 'personal' })}>新增个人 API</Button>}{team && can('api.manage') && <Button size="sm" variant="outline" onClick={() => setForm({ scope: 'team' })}>新增团队公共 API</Button>}</div></div>
    <p className="text-sm text-muted-foreground">选择一个默认 API 用于 AI 回答、深度AI、标题和你发起的文档摘要。公共 API 需授权，调用失败不会自动换用其他人的 API。向量模型由系统统一配置。</p>
    {providers.error && <p className="text-destructive">{providers.error.message}</p>}
    {form && <ProviderForm key={form.initial?.id || form.scope} {...form} onClose={() => setForm(null)} />}
    {!providers.isLoading && !providers.data?.items.length && <p className="rounded border border-dashed p-6 text-center text-sm text-muted-foreground">还没有可用 API。可以添加个人配置，或请管理员授权团队 API。</p>}
    {providers.data?.items.filter(p => team ? p.scope === 'team' : p.scope === 'personal' || p.usable).map(p => <div key={p.id} className="min-w-0 rounded-lg border p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="font-medium">{p.name}</span><span className="rounded bg-muted px-2 py-0.5 text-xs">{p.scope === 'team' ? '团队公共' : '个人私有'}</span>{p.id === providers.data.default_provider && <span className="text-xs text-primary">当前默认</span>}</div><p className="mt-1 break-all text-xs text-muted-foreground">{p.chat_model} · {p.protocol}</p><p className="mt-1 break-all text-xs text-muted-foreground">{p.base_url}</p><p className="mt-1 text-xs text-muted-foreground">密钥：{p.has_api_key ? '已配置' : '未配置'}</p></div>{p.manageable && (team || p.scope === 'personal') && <Toggle label={`启用 ${p.name}`} checked={!!p.enabled} disabled={busy === p.id} onChange={() => act(p.id, 'toggle')} />}</div><div className="mt-3 flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={!p.usable || busy === p.id || p.id === providers.data.default_provider} onClick={() => act(p.id, 'choose')}>设为默认</Button><Button size="sm" variant="ghost" disabled={!p.usable || busy === p.id} onClick={() => act(p.id, 'test')}>测试连接</Button><Button size="sm" variant="ghost" disabled={!p.usable || busy === p.id} onClick={() => act(p.id, 'test-tools')}>{busy === p.id ? '处理中…' : '检测工具调用支持'}</Button>{p.manageable && (team || p.scope === 'personal') && <><Button size="sm" variant="ghost" onClick={() => setForm({ scope: p.scope, initial: p })}>编辑</Button><Button size="sm" variant="ghost" disabled={busy === p.id} onClick={() => act(p.id, 'delete')}>删除</Button></>}{team && p.grantable && <Grants path={`/providers/${p.id}`} />}</div></div>)}
  </Card>
}

export function AccountPage() {
  const { user, logout, can } = useAuth()
  const [thinking, setThinking] = useLocalStorage('amd-ui-show-thinking', true)
  const [showContext, setShowContext] = useLocalStorage('amd-ui-show-context', false)
  const [citations, setCitations] = useLocalStorage('amd-ui-show-citations', false)
  const [logs, setLogs] = useLocalStorage('amd-ui-show-logs', false)
  const [apiRetryCount, setApiRetryCount] = useApiRetryCount()
  return <div className="mx-auto max-w-4xl space-y-5 p-6 md:p-8"><div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-semibold">设置</h1><p className="mt-1 text-sm text-muted-foreground">{user.username} · {user.roles.filter(r => r.enabled).map(r => r.name).join('、') || '无启用角色'}</p></div></div>
    <ProviderSettings />
    <PersonalPreferencesCard />
    <Card className="space-y-3 p-5"><h2 className="font-semibold">AI 回答设置</h2><p className="text-sm text-muted-foreground">AI约束策略适用于增强AI和深度AI。增强AI 一次检索后直接回答；深度AI 主动查阅知识库，需要 API 支持工具调用。下方查阅轮数与总时长设置仅用于深度AI。</p><DeepAiOptionsFields />
    </Card>
    <Card className="space-y-3 p-5"><h2 className="font-semibold">API 请求重试</h2>
      <div className="flex items-center justify-between gap-4 text-sm"><span>API 失败重试次数</span><OptionSelect aria-label="API 失败重试次数" value={String(apiRetryCount)} onValueChange={value => setApiRetryCount(Number(value))} options={Array.from({ length: 11 }, (_, value) => ({ value: String(value), label: value === 0 ? '不重试' : `${value} 次${value === 10 ? '（默认）' : ''}` }))} /></div>
      <p className="text-xs text-muted-foreground">自动保存，适用于 增强AI和深度AI。默认最多重试 10 次，间隔约 1、2、4、8、16、30 秒，之后最多 30 秒。所有尝试和等待都计入时间预算，次数不保证用完。已输出内容后中断会保留部分答案；服务要求等待超过 30 秒的限流或暂不可用错误，会提示稍后重试。</p>
    </Card>
    <Card className="space-y-4 p-5"><h2 className="font-semibold">显示偏好</h2><div className="flex items-center justify-between gap-4 text-sm"><span>显示上下文使用情况（可展开）</span><Toggle label="显示上下文使用情况" checked={showContext} onChange={setShowContext} /></div><div className="flex items-center justify-between gap-4 text-sm"><span>展示思考过程（默认折叠）</span><Toggle label="展示思考过程" checked={thinking} onChange={setThinking} /></div><div className="flex items-center justify-between gap-4 text-sm"><span>显示引用编号和引用来源</span><Toggle label="显示引用编号和引用来源" checked={citations} onChange={setCitations} /></div>{can('logs.view') && <div className="flex items-center justify-between gap-4 text-sm"><span>在侧边栏显示「系统日志」入口</span><Toggle label="显示系统日志入口" checked={logs} onChange={setLogs} /></div>}</Card>
    <Card className="p-5"><h2 className="mb-4 font-semibold">修改密码</h2><div className="max-w-md"><PasswordForm onDone={logout} /></div></Card>
  </div>
}
