import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Plug } from 'lucide-react'
import { accountApi, accountRequest, type ApiModel, type Provider } from '@/lib/account-api'
import { accountInput, useAuth } from '@/hooks/use-auth'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { OptionSelect } from '@/components/ui/select'
import { Toggle } from '@/components/ui/toggle-switch'
import { ManagementDialog } from '@/components/management-dialog'
import { AccessEditor } from '@/components/access-editor'
import { useConfirm } from '@/components/confirmation-provider'
import { ApiModelSelect } from '@/components/api-model-select'

const providerQueryKey = ['account-providers']
async function refreshApiQueries(qc: ReturnType<typeof useQueryClient>) {
  await Promise.all([
    qc.invalidateQueries({ queryKey: providerQueryKey }),
    qc.invalidateQueries({ queryKey: ['api-preferences'] }),
    qc.invalidateQueries({ queryKey: ['session-model'] }),
    qc.invalidateQueries({ queryKey: ['llm-providers'] }),
  ])
}

function ProviderForm({ initial, scope, onClose }: { initial?: Provider; scope: 'personal' | 'team'; onClose: () => void }) {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [name, setName] = useState(initial?.name || '')
  const [protocol, setProtocol] = useState(initial?.protocol || 'openai')
  const [url, setUrl] = useState(initial?.base_url || '')
  const [models, setModels] = useState<ApiModel[]>(initial?.models || [{ model_id: '', display_name: '' }])
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  function patch(index: number, field: 'model_id' | 'display_name', value: string) {
    setModels(models.map((m, i) => i === index ? { ...m, [field]: value } : m))
  }
  async function save(event: FormEvent) {
    event.preventDefault()
    if (initial?.models.some(old => !models.some(m => m.model_id.trim() === old.model_id)) &&
        !await confirm('移除或修改模型 ID 后，使用这些模型的默认选择、会话及任务将需要重新选择。继续保存？')) return
    setBusy(true); setError('')
    try {
      await accountRequest(`/providers${initial ? '/' + initial.id : ''}`, initial ? 'PUT' : 'POST', {
        name, scope, protocol, base_url: url, models: models.map(m => ({ model_id: m.model_id, display_name: m.display_name, context_tokens: m.context_tokens, output_tokens: m.output_tokens })),
        api_key: key || (initial ? null : ''), enabled: initial?.enabled ?? true,
      })
      await refreshApiQueries(qc); onClose(); toast.success('API 配置已保存')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <ManagementDialog title={`${initial ? '编辑' : '新增'}${scope === 'team' ? '团队' : '个人'} API`} busy={busy} onClose={onClose}>
    <form onSubmit={save}><fieldset disabled={busy} className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2"><label className="text-sm">API 名称<input className={accountInput} value={name} onChange={e => setName(e.target.value)} required maxLength={80} /></label>
        <label className="text-sm">协议<OptionSelect aria-label="协议" value={protocol} onValueChange={setProtocol} options={[{ value: 'openai', label: 'OpenAI 兼容' }, { value: 'anthropic', label: 'Anthropic' }, { value: 'anthropic-arch', label: 'Anthropic Arch' }]} /></label></div>
      <label className="block text-sm">API 地址<input className={accountInput} type="url" value={url} onChange={e => setUrl(e.target.value)} required maxLength={2048} placeholder="https://api.example.com/v1" /></label>
      <p className="text-xs text-muted-foreground">同一 API 下的模型共用地址、协议和密钥。OpenAI 兼容地址只填域名时使用 /v1，自定义路径请填写完整地址。</p>
      <label className="block text-sm">API 密钥<input className={accountInput} type="password" autoComplete="new-password" value={key} onChange={e => setKey(e.target.value)} maxLength={8192} placeholder={initial?.has_api_key ? '已保存；留空保留原密钥' : '服务无密钥要求时可留空'} /></label>
      <div className="space-y-3"><p className="text-sm font-medium">模型列表</p>{models.map((model, i) => <div key={i} className="space-y-2 rounded border p-3">
        <label className="block text-sm">模型 ID<input className={accountInput} value={model.model_id} onChange={e => patch(i, 'model_id', e.target.value)} required maxLength={200} placeholder="服务商提供的准确模型 ID" /></label>
        <label className="block text-sm">显示名称（可选）<input className={accountInput} value={model.display_name} onChange={e => patch(i, 'display_name', e.target.value)} maxLength={80} placeholder="聊天选择器中显示的名称" /></label>
        <details className="col-span-full text-xs text-muted-foreground"><summary className="cursor-pointer">模型能力（可选，摘要预算使用）</summary><p className="my-2">按服务商文档填写；留空表示未知，不根据模型名称推测。填写值不代表服务商保证。</p><div className="grid gap-3 sm:grid-cols-2">
          {(['context_tokens', 'output_tokens'] as const).map(key => <label key={key}>{key === 'context_tokens' ? '上下文容量（tokens）' : '最大输出（tokens）'}<input type="number" className={accountInput} value={model[key] ?? ''} min={key === 'context_tokens' ? 4096 : 256} onChange={e => setModels(items => items.map((item, index) => index === i ? { ...item, [key]: e.target.value === '' ? null : Number(e.target.value) } : item))} /></label>)}
        </div></details>
        <Button type="button" size="sm" variant="ghost" disabled={models.length === 1} onClick={() => setModels(models.filter((_, n) => n !== i))}>移除此模型</Button>
      </div>)}<Button type="button" variant="outline" size="sm" disabled={models.length >= 100} onClick={() => setModels([...models, { model_id: '', display_name: '' }])}>添加模型</Button></div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex gap-2"><Button type="submit">保存配置</Button><Button type="button" variant="outline" onClick={onClose}>取消</Button></div>
    </fieldset></form>
  </ManagementDialog>
}

function TestResult({ model, kind }: { model: ApiModel; kind: 'connection' | 'tools' }) {
  const state = model[`${kind}_status`]
  const time = model[`${kind}_checked_at`]
  const label = kind === 'connection' ? '连接' : '工具调用'
  return <p className="text-xs text-muted-foreground" title={model[`${kind}_message`] || undefined}>{label}：{state === 'passed' ? '上次检测通过' : state === 'failed' ? '上次检测失败' : state === 'unknown' ? '上次未能确认' : '未检测'}{time ? ` · ${new Date(time * 1000).toLocaleString()}` : ''}</p>
}

export function ProviderSettings({ team = false }: { team?: boolean }) {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { can } = useAuth()
  const providers = useQuery({ queryKey: providerQueryKey, queryFn: accountApi.providers })
  const [form, setForm] = useState<{ scope: 'personal' | 'team'; initial?: Provider } | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState('')
  const [grants, setGrants] = useState<Provider | null>(null)
  async function run(action: () => Promise<unknown>, success?: string) {
    setBusy(true); setFailure('')
    try { await action(); if (success) toast.success(success) }
    catch (e) { setFailure((e as Error).message) }
    finally { await refreshApiQueries(qc); setBusy(false) }
  }
  async function toggle(p: Provider) {
    if (p.enabled && !await confirm(p.scope === 'team' ? '停用后所有成员都无法继续调用此 API，正在运行的任务也会在权限检查时停止使用它。继续？' : '暂停后默认选择、会话和任务将不能继续调用此 API。继续？')) return
    await run(() => accountRequest(`/providers/${p.id}`, 'PUT', { ...p, api_key: null, enabled: !p.enabled }), p.enabled ? '已暂停使用' : '已恢复使用')
  }
  const items = providers.data?.items || []
  const groups = team
    ? [{ title: '团队 API 配置', description: '管理连接、模型列表和成员使用权。个人默认模型请在个人设置中选择。', items: items.filter(p => p.scope === 'team'), canCreate: can('api.manage') }]
    : [
      { title: '我的 API 配置', description: '保存连接信息与模型列表；保存配置不会自动改变当前使用的模型。', items: items.filter(p => p.scope === 'personal'), canCreate: can('api.personal') },
      { title: '已授权的团队 API', description: '可以选择和检测模型。连接信息及模型列表由团队管理员维护。', items: items.filter(p => p.scope === 'team' && p.usable), canCreate: false },
    ]
  return <div className="space-y-4">
    {!team && <Card className="space-y-3 p-5"><h2 className="font-semibold">我的默认模型</h2>
      <p className="text-sm text-muted-foreground">用于新会话和之后发起的 AI 摘要任务。聊天中可以单独切换会话模型；失败不会自动换用其他 API。</p>
      <ApiModelSelect providers={items} selection={providers.data?.selection} disabled={busy || providers.isLoading} onChange={(p, m) => void run(() => accountApi.choose(p, m), '默认模型已更新')} />
      {providers.data?.selection.valid && <p className="text-xs text-muted-foreground">来源：{providers.data.selection.provider_name}</p>}
      {providers.data && !providers.data.selection.valid && <p role="alert" className="text-sm text-destructive">{providers.data.selection.reason}</p>}
    </Card>}
    {(failure || providers.error) && <p role="alert" className="rounded border border-destructive/30 p-3 text-sm text-destructive">{failure || providers.error?.message}</p>}
    {groups.map(group => <Card key={group.title} className="space-y-4 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">{group.title}</h2>
        {group.canCreate && <Button size="sm" onClick={() => setForm({ scope: team ? 'team' : 'personal' })}>新增 API</Button>}</div>
      <p className="text-sm text-muted-foreground">{group.description}</p>
      {!providers.isLoading && group.items.length === 0 && <p className="text-sm text-muted-foreground">{group.title === '已授权的团队 API' ? '暂无已授权且启用的团队 API。' : '暂无 API 配置。'}</p>}
      {group.items.map(p => {
        const editable = p.manageable && (team || p.scope === 'personal')
        return <section key={p.id} className="rounded-lg border p-4">
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
            <div className="min-w-0 flex-1 basis-56">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <h3 className="flex min-w-0 items-center gap-2 font-medium"><Plug aria-hidden="true" className="h-4 w-4 shrink-0 text-muted-foreground" /><span className="min-w-0 break-words">{p.name} {!p.enabled && !(team && p.manageable) && <span className="text-xs text-muted-foreground">（已暂停）</span>}</span></h3>
                {team && p.manageable && <label className="flex shrink-0 items-center gap-2 whitespace-nowrap text-xs text-muted-foreground"><Toggle label={`启用 ${p.name}`} checked={!!p.enabled} disabled={busy} onChange={() => void toggle(p)} />{p.enabled ? '服务已启用' : '服务已停用'}</label>}
              </div>
              <p className="mt-1 break-all text-xs text-muted-foreground">{p.base_url} · {p.protocol}</p>
            </div>
            <div className="flex max-w-full shrink-0 flex-wrap items-center gap-1">
              {team && p.grantable && <Button size="sm" variant="outline" onClick={() => setGrants(p)}>使用授权</Button>}
              {editable && <Button size="sm" variant="outline" disabled={busy} onClick={() => setForm({ scope: p.scope, initial: p })}>编辑</Button>}
              {editable && <DropdownMenu.Root>
                <DropdownMenu.Trigger asChild><Button size="sm" variant="ghost" disabled={busy} aria-label={`${p.name} 更多操作`}>更多</Button></DropdownMenu.Trigger>
                <DropdownMenu.Portal><DropdownMenu.Content align="end" sideOffset={4} collisionPadding={8} className="z-[90] min-w-32 rounded-md border bg-popover p-1 text-sm text-popover-foreground shadow-md">
                  {!team && <DropdownMenu.Item className="cursor-pointer rounded px-3 py-2 outline-none focus:bg-accent" onSelect={() => void toggle(p)}>{p.enabled ? '暂停使用' : '恢复使用'}</DropdownMenu.Item>}
                  <DropdownMenu.Item className="cursor-pointer rounded px-3 py-2 text-destructive outline-none focus:bg-accent" onSelect={() => void (async () => { if (await confirm('删除 API 及其模型？相关默认选择和会话将需要重新选择，历史回答保留。')) await run(() => accountRequest(`/providers/${p.id}`, 'DELETE'), 'API 已删除') })()}>删除 API</DropdownMenu.Item>
                </DropdownMenu.Content></DropdownMenu.Portal>
              </DropdownMenu.Root>}
            </div>
          </div>
          {!p.usable && <p className="mt-2 text-xs text-muted-foreground">{!p.enabled ? '恢复使用后才能选择或检测模型。' : '没有调用授权，不能选择或检测；管理配置不自动获得调用权。'}</p>}
          <div className="mb-1 ml-2 mt-5 border-l pl-3 sm:pl-5">
            <p className="mb-1 text-xs text-muted-foreground">模型 · {p.models.length}</p>
            <div className="divide-y">{p.models.map(m => <div key={m.model_id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3">
            <div className="min-w-0 flex-1 basis-40"><p className="break-all text-sm font-normal">{m.display_name || m.model_id}</p>{m.display_name && <p className="break-all text-xs text-muted-foreground">{m.model_id}</p>}
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1"><TestResult model={m} kind="connection" /><TestResult model={m} kind="tools" /></div>
            </div>
            <div className="flex max-w-full shrink-0 flex-wrap items-center gap-1">
              <Button size="sm" variant="ghost" disabled={busy || !p.usable} onClick={() => void run(() => accountRequest(`/providers/${p.id}/test?model_id=${encodeURIComponent(m.model_id)}`, 'POST'), '连接测试成功')}>测试连接</Button>
              <Button size="sm" variant="ghost" disabled={busy || !p.usable} onClick={() => void run(async () => { const result = await accountRequest<{ supported: boolean | null; message: string }>(`/providers/${p.id}/test-tools?model_id=${encodeURIComponent(m.model_id)}`, 'POST'); if (!result.supported) throw new Error(result.message); toast.success(result.message) })}>检测工具调用</Button>
            </div>
          </div>)}</div>
          </div>
        </section>
      })}
    </Card>)}
    {form && <ProviderForm key={form.initial?.id || form.scope} {...form} onClose={() => setForm(null)} />}
    {grants && <ManagementDialog title={`${grants.name} · 使用授权`} onClose={() => { setGrants(null); void refreshApiQueries(qc) }}><AccessEditor kind="api" id={grants.id} /></ManagementDialog>}
  </div>
}
