import { api } from '@/lib/api'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Plug, Check, ChevronDown } from 'lucide-react'
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
import { modelEditMessage, type ModelEditImpact } from '@/lib/model-edit'
import { WebSearchTestDialog, type WebSearchTest } from '@/components/web-search-test-dialog'

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
  const { refreshAccount } = useAuth()
  const confirm = useConfirm()
  const [name, setName] = useState(initial?.name || '')
  const [protocol, setProtocol] = useState(initial?.protocol || 'openai')
  const [url, setUrl] = useState(initial?.base_url || '')
  const [models, setModels] = useState<ApiModel[]>(initial?.models.map(m => ({ ...m, original_model_id: m.model_id })) || [{ model_id: '', display_name: '' }])
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  function patch(index: number, field: 'model_id' | 'display_name', value: string) {
    setModels(models.map((m, i) => i === index ? { ...m, [field]: value } : m))
  }
  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      const body = {
        name, scope, protocol, base_url: url,
        models: models.map(m => ({ model_id: m.model_id, original_model_id: m.original_model_id, display_name: m.display_name, context_tokens: m.context_tokens, output_tokens: m.output_tokens, web_search: m.web_search ?? 'off' })),
        api_key: key || (initial ? null : ''), enabled: initial?.enabled ?? true,
        edit_version: initial?.edit_version,
      }
      let confirmation_token: string | undefined
      if (initial) {
        const impact = await accountRequest<ModelEditImpact>(`/providers/${initial.id}/edit-preview`, 'POST', body)
        const message = modelEditMessage(impact, scope === 'team')
        if (message && !await confirm(message)) return
        confirmation_token = impact.confirmation_token
      }
      await accountRequest(`/providers${initial ? '/' + initial.id : ''}`, initial ? 'PUT' : 'POST', { ...body, confirmation_token })
      await refreshAccount()
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
        <p className="text-xs text-muted-foreground">修改已有模型 ID 时，确认后同步默认选择与会话；删除后再添加视为不同模型。</p>
        <label className="block text-sm">显示名称（可选）<input className={accountInput} value={model.display_name} onChange={e => patch(i, 'display_name', e.target.value)} maxLength={80} placeholder="聊天选择器中显示的名称" /></label>
        <p className="text-xs text-muted-foreground">显示名称仅影响展示，不改变实际调用的模型。</p>
        <label className="block text-sm">联网搜索（深度 AI）<OptionSelect aria-label={`模型 ${i + 1} 联网搜索`} value={model.web_search ?? 'off'} onValueChange={value => setModels(items => items.map((item, index) => index === i ? { ...item, web_search: value as ApiModel['web_search'] } : item))} options={[{ value: 'off', label: '不启用' }, ...(protocol === 'openai' ? [{ value: 'responses', label: '启用 · Responses 搜索' }] : [{ value: 'anthropic', label: '启用 · Anthropic 搜索' }])]} /></label>
        <p className="text-xs text-muted-foreground">需同时开启系统联网搜索。Responses 模式仅用于深度 AI，该模型的普通连接测试沿用 API 协议；启用后请测试联网能力。</p>
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

function CompactStatus({ label, value, passed, detail, onClick }: { label: string; value: string; passed?: boolean; detail: string; onClick?: () => void }) {
  const content = <>{label}{passed ? <Check aria-hidden="true" className="h-3 w-3 text-emerald-600 dark:text-emerald-400" /> : <span>{value}</span>}</>
  const style = 'inline-flex items-center gap-1 whitespace-nowrap rounded py-1 text-[11px] text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
  if (onClick) return <button type="button" className={style} title={detail} aria-label={`${label}：${value}，查看详情`} onClick={onClick}>{content}</button>
  return <DropdownMenu.Root modal={false}><DropdownMenu.Trigger asChild><button type="button" className={style} title={detail} aria-label={`${label}：${value}，查看详情`}>{content}</button></DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content align="start" sideOffset={4} collisionPadding={8} className="z-[90] max-w-[min(320px,calc(100vw-32px))] whitespace-pre-line break-words rounded-md border bg-popover p-3 text-xs leading-relaxed text-popover-foreground shadow-md">{detail}</DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root>
}

function TestResult({ model, kind }: { model: ApiModel; kind: 'connection' | 'tools' }) {
  const state = model[`${kind}_status`]
  const time = model[`${kind}_checked_at`]
  const label = kind === 'connection' ? '连接' : '工具'
  const value = state === 'passed' ? '通过' : state === 'failed' ? '失败' : state === 'unknown' ? '未确认' : '未检测'
  const detail = `${label}：${value}${time ? `\n检测时间：${new Date(time * 1000).toLocaleString()}` : ''}${model[`${kind}_message`] ? `\n${model[`${kind}_message`]}` : ''}`
  return <CompactStatus label={label} value={value} passed={state === 'passed'} detail={detail} />
}

export function ProviderSettings({ team = false }: { team?: boolean }) {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { can, refreshAccount } = useAuth()
  const webStatus = useQuery({ queryKey: ['web-search-status'], queryFn: api.webSearch.get })
  const providers = useQuery({ queryKey: providerQueryKey, queryFn: accountApi.providers })
  const [form, setForm] = useState<{ scope: 'personal' | 'team'; initial?: Provider } | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState('')
  const [testFailure, setTestFailure] = useState<{ title: string; message: string } | null>(null)
  const [webTest, setWebTest] = useState<WebSearchTest | null>(null)
  const [showWebTest, setShowWebTest] = useState(false)
  const webController = useRef<AbortController | null>(null)
  useEffect(() => () => { webController.current?.abort() }, [])
  function stopWebSearch() {
    webController.current?.abort()
    setWebTest(previous => previous?.status === 'pending' ? { ...previous, status: 'cancelled', elapsedMs: Date.now() - previous.startedAt,
      message: '已停止等待并断开本次测试请求，不计为测试失败。服务端检测到断开后会取消任务；上游是否停止计算或计费取决于服务商。' } : previous)
  }
  const [grants, setGrants] = useState<Provider | null>(null)
  async function testWebSearch(provider: Provider, model: ApiModel) {
    if (busy || webController.current) return
    const controller = new AbortController()
    webController.current = controller
    const startedAt = Date.now()
    const initial: WebSearchTest = { providerId: provider.id, modelId: model.model_id,
      label: `${provider.name} · ${model.display_name || model.model_id}`, startedAt,
      status: 'pending', message: '正在确认模型配置与使用权限…', sources: [] }
    setWebTest(initial); setShowWebTest(true); setBusy(true); setFailure('')
    try {
      await refreshAccount()
      controller.signal.throwIfAborted()
      setWebTest({ ...initial, message: '正在请求模型执行搜索，并验证返回的网页来源…' })
      const result = await accountRequest<{ supported: boolean; message: string; sources: { title: string; url: string }[] }>(`/providers/${provider.id}/test-web-search?model_id=${encodeURIComponent(model.model_id)}`, 'POST', undefined, controller.signal)
      controller.signal.throwIfAborted()
      setWebTest({ ...initial, ...result, status: result.supported ? 'success' : 'unconfirmed', elapsedMs: Date.now() - startedAt })
    } catch (error) {
      setWebTest({ ...initial, status: controller.signal.aborted ? 'cancelled' : 'error', message: controller.signal.aborted ? '已停止等待并断开本次测试请求，不计为测试失败。服务端检测到断开后会取消任务；上游是否停止计算或计费取决于服务商。' : (error as Error).message || '请求未完成，请稍后重试', elapsedMs: Date.now() - startedAt })
    } finally { webController.current = null; setBusy(false) }
  }
  async function run(action: () => Promise<unknown>, success?: string, testTitle?: string) {
    setBusy(true); setFailure('')
    try { await refreshAccount(); await action(); await refreshAccount(); if (success) toast.success(success) }
    catch (e) {
      if (testTitle) setTestFailure({ title: testTitle, message: (e as Error).message || '请求未完成，请稍后重试' })
      else setFailure((e as Error).message)
    }
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
    {testFailure && <ManagementDialog title={testFailure.title} className="max-w-xl" onClose={() => setTestFailure(null)}><div role="alert" className="rounded-lg border border-destructive/20 bg-destructive/5 p-4"><p className="font-medium text-destructive">检测未通过</p><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed">{testFailure.message}</p></div><p className="text-xs text-muted-foreground">本次检测未成功完成，请根据提示检查配置后手动重试。</p><Button variant="outline" onClick={() => setTestFailure(null)}>关闭</Button></ManagementDialog>}
    {showWebTest && webTest && <WebSearchTestDialog test={webTest} onClose={() => setShowWebTest(false)} onStop={stopWebSearch} />}
    {!team && <Card className="space-y-3 p-5"><h2 className="font-semibold">我的默认模型</h2>
      <p className="text-sm text-muted-foreground">用于新会话和之后发起的 AI 摘要任务。聊天中可以单独切换会话模型；失败不会自动换用其他 API。</p>
      <ApiModelSelect providers={items} selection={providers.data?.selection} disabled={busy || providers.isLoading} onChange={(p, m) => void run(() => accountApi.choose(p, m), '默认模型已更新')} />
      {providers.data?.selection.valid && <p className="text-xs text-muted-foreground">来源：{providers.data.selection.provider_name}</p>}
      {providers.data && !providers.data.selection.valid && <p role="alert" className="text-sm text-destructive">{providers.data.selection.reason}</p>}
    </Card>}
    {(failure || providers.error) && <p role="alert" className="rounded border border-destructive/30 p-3 text-sm text-destructive">{failure || providers.error?.message}</p>}
    <p className="text-xs text-muted-foreground">联网搜索需同时启用系统总开关与模型配置；检测不受系统开关影响，点击状态可查看详情。</p>
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
            <div className="divide-y">{p.models.map(m => <div key={m.model_id} className="flex items-start justify-between gap-4 py-3">
            <div className="min-w-0 flex-1"><p className="flex min-h-8 items-center break-all text-sm font-normal leading-5">{m.display_name || m.model_id}</p>{m.display_name && <p className="break-all text-xs text-muted-foreground">{m.model_id}</p>}
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5"><TestResult model={m} kind="connection" /><TestResult model={m} kind="tools" />
                {webTest?.providerId === p.id && webTest.modelId === m.model_id
                  ? <CompactStatus label="联网" value={webTest.status === 'pending' ? '测试中…' : !m.web_search || m.web_search === 'off' ? '未配置' : webStatus.data ? webStatus.data.enabled ? '已启用' : '已配置·系统未开启' : '系统状态未知'} detail={`${webTest.message}\n${webStatus.data ? webStatus.data.enabled ? '系统总开关已开启' : '系统总开关未开启，问答中不生效' : '系统开关状态未知'}\n检测不受系统开关影响；本次检测结果刷新后不保留`} onClick={() => setShowWebTest(true)} />
                  : <CompactStatus label="联网" value={m.web_search && m.web_search !== 'off' ? webStatus.data ? webStatus.data.enabled ? '已启用' : '已配置·系统未开启' : '系统状态未知' : '未配置'} detail={m.web_search && m.web_search !== 'off' ? '已配置服务端搜索方式，尚无本次页面的测试结果。配置不代表检测通过；实际问答还需开启系统总开关。' : '此模型未启用联网搜索，可在 API 编辑窗口配置。'} />}
              </div>
            </div>
            <div className="flex shrink-0 items-start">
              <DropdownMenu.Root modal={false}>
                <DropdownMenu.Trigger asChild><Button size="sm" variant="ghost" className="text-sm font-normal leading-5" disabled={busy || !p.usable} aria-label={`${m.model_id} 检测`}>检测<ChevronDown aria-hidden="true" className="ml-1 h-3 w-3" /></Button></DropdownMenu.Trigger>
                <DropdownMenu.Portal><DropdownMenu.Content align="end" sideOffset={4} collisionPadding={8} className="z-[90] min-w-36 rounded-md border bg-popover p-1 text-sm text-popover-foreground shadow-md">
                  <DropdownMenu.Item className="cursor-pointer rounded px-3 py-2 outline-none focus:bg-accent" onSelect={() => void run(() => accountRequest(`/providers/${p.id}/test?model_id=${encodeURIComponent(m.model_id)}`, 'POST'), '连接测试成功', `${p.name} · ${m.model_id} · 连接测试`)}>测试连接</DropdownMenu.Item>
                  <DropdownMenu.Item className="cursor-pointer rounded px-3 py-2 outline-none focus:bg-accent" onSelect={() => void run(async () => { const result = await accountRequest<{ supported: boolean | null; message: string }>(`/providers/${p.id}/test-tools?model_id=${encodeURIComponent(m.model_id)}`, 'POST'); if (!result.supported) throw new Error(result.message); toast.success(result.message) }, undefined, `${p.name} · ${m.model_id} · 工具调用检测`)}>检测工具调用</DropdownMenu.Item>
                  <DropdownMenu.Item disabled={!m.web_search || m.web_search === 'off'} className="cursor-pointer rounded px-3 py-2 outline-none focus:bg-accent data-[disabled]:opacity-50 data-[disabled]:pointer-events-none" onSelect={() => void testWebSearch(p, m)}>测试联网搜索</DropdownMenu.Item>
                </DropdownMenu.Content></DropdownMenu.Portal>
              </DropdownMenu.Root>
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
