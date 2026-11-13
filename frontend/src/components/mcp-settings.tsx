import { SettingsHelp } from '@/components/settings-help'
import { useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Copy, Loader2, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { useAuth, accountInput } from '@/hooks/use-auth'
import { accountRequest } from '@/lib/account-api'
import { copyText } from '@/lib/clipboard'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { OptionSelect } from '@/components/ui/select'
import { Toggle } from '@/components/ui/toggle-switch'
import { ManagementDialog } from '@/components/management-dialog'
import { ConfirmDialog } from '@/components/confirm-dialog'

type Token = { id: string; user_id: string; name: string; prefix: string; kb_ids: string[]; created_at: number; expires_at: number; revoked_at: number | null; last_used_at: number | null; username?: string }
type Personal = { enabled: boolean; can_use: boolean; public_url: string; items: Token[]; knowledge_bases: { id: string; name: string }[] }
type Configuration = { enabled: boolean; public_url: string; timeout_seconds: number; max_concurrent: number; max_output_chars: number; requests_per_minute: number; reference_minutes: number; rerank_enabled: boolean; rerank_candidates: number; rerank_seconds: number; rerank_min_score: number }
type Call = { id: number; username: string | null; display_name: string | null; token_name: string | null; tool: string; kb_id: string | null; status: string; duration_ms: number; created_at: number }
const date = (seconds: number | null) => seconds ? new Date(seconds * 1000).toLocaleString() : '—'
const callState = (status: string) => ({ ok: '成功', '408': '已取消或超时', '401': '令牌失效', '403': '无权访问', '413': '结果超限', '422': '参数错误', '503': '接入已关闭' } as Record<string, string>)[status] ?? '查询失败'
const state = (token: Token) => token.revoked_at ? '已撤销' : token.expires_at <= Date.now() / 1000 ? '已过期' : '有效'

function ErrorLine({ error, retry }: { error: Error; retry: () => void }) {
  return <p role="alert" className="text-sm text-destructive">{error.message}<Button variant="ghost" size="sm" onClick={retry}>重试</Button></p>
}

function CodeSample({ title, value }: { title: string; value: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try { await copyText(value); setCopied(true); window.setTimeout(() => setCopied(false), 1800) }
    catch { toast.error('复制失败，请手动选择并复制') }
  }
  return <div className="min-w-0 overflow-hidden rounded-lg border">
    <div className="flex items-center justify-between bg-muted/30 px-3 py-2 text-xs"><span>{title}</span><Button size="sm" variant="ghost" className="h-7 gap-1" onClick={() => void copy()}><Copy className="h-3 w-3" />{copied ? '已复制' : '复制'}</Button></div>
    <pre className="overflow-x-auto p-3 text-xs leading-relaxed">{value}</pre>
  </div>
}

function ConnectionExamples({ url }: { url: string }) {
  if (!url) return <p role="status" className="text-sm text-muted-foreground"><SettingsHelp title="管理员尚未填写 MCP 接入地址。">请先完成独立网关部署，再到系统设置 → MCP 接入填写地址。</SettingsHelp></p>
  return <div className="space-y-3">
    <p className="break-all text-sm"><SettingsHelp title={<>接入地址：{url}</>}>将令牌设置为启动 Agent 时的 INSIGHT_MCP_TOKEN 环境变量。配置文件使用变量引用，不包含令牌明文；已运行的客户端需要重新加载配置。</SettingsHelp></p>
    <CodeSample title="Codex · config.toml" value={`[mcp_servers.insight]\nurl = ${JSON.stringify(url)}\nbearer_token_env_var = "INSIGHT_MCP_TOKEN"\ntool_timeout_sec = 135`} />
    <CodeSample title="Claude Code · .mcp.json" value={JSON.stringify({ mcpServers: { insight: { type: 'http', url, headers: { Authorization: 'Bearer ${INSIGHT_MCP_TOKEN}' } } } }, null, 2)} />
  </div>
}

function CreateToken({ data, onClose, onCreated }: { data: Personal; onClose: () => void; onCreated: (secret: string) => void }) {
  const [name, setName] = useState('')
  const [days, setDays] = useState('30')
  const [selected, setSelected] = useState<string[]>([])
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!selected.length) { toast.error('请至少选择一个知识库'); return }
    setBusy(true)
    try {
      // Keep the one-time secret out of the query/mutation cache and browser storage.
      const result = await accountRequest<{ token: string }>('/account/mcp/tokens', 'POST', { name, kb_ids: selected, days: Number(days) })
      onCreated(result.token)
    } catch (error) { toast.error((error as Error).message) }
    finally { setBusy(false) }
  }
  return <ManagementDialog title="创建 MCP 令牌" onClose={onClose} busy={busy} className="max-w-lg"><form onSubmit={event => void submit(event)} className="space-y-4">
    <label className="block space-y-1 text-sm"><span>名称</span><input className={accountInput} value={name} onChange={event => setName(event.target.value)} required maxLength={80} placeholder="例如：办公电脑 Codex" /></label>
    <label className="block space-y-1 text-sm"><span>有效期</span><OptionSelect aria-label="令牌有效期" value={days} onValueChange={setDays} options={[7, 30, 90, 365].map(value => ({ value: String(value), label: `${value} 天` }))} /></label>
    <div className="space-y-2"><p className="text-sm"><SettingsHelp title={<>知识库范围 · 已选 {selected.length}/100</>}>令牌只能读取所选范围，不能修改文档。用户权限被收回后，令牌也无法继续查询对应知识库。令牌仅在创建后显示一次。</SettingsHelp></p><input className={accountInput} aria-label="筛选知识库" placeholder="搜索知识库" value={filter} onChange={event => setFilter(event.target.value)} />
      <div className="max-h-52 space-y-1 overflow-y-auto rounded-lg border p-2">{data.knowledge_bases.filter(kb => kb.name.toLowerCase().includes(filter.toLowerCase())).map(kb => <label key={kb.id} className="flex cursor-pointer items-start gap-2 rounded-md p-2 text-sm hover:bg-accent/30"><input type="checkbox" className="mt-0.5 accent-primary" checked={selected.includes(kb.id)} disabled={!selected.includes(kb.id) && selected.length >= 100} onChange={event => setSelected(current => event.target.checked ? [...current, kb.id] : current.filter(id => id !== kb.id))} /><span className="break-all">{kb.name}</span></label>)}{!data.knowledge_bases.length && <p className="p-2 text-sm text-muted-foreground">暂无可授权知识库</p>}</div>
    </div>

    <div className="flex justify-end gap-2"><Button type="button" variant="outline" disabled={busy} onClick={onClose}>取消</Button><Button type="submit" disabled={busy || !selected.length}>{busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}{busy ? '正在创建…' : '创建令牌'}</Button></div>
  </form></ManagementDialog>
}

export function PersonalMcpSettings() {
  const { user } = useAuth()
  return <PersonalMcpContent key={`${user.id}:${user.permission_revision}`} />
}
function PersonalMcpContent() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const key = ['mcp-personal', user.id]
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => accountRequest<Personal>('/account/mcp', 'GET', undefined, signal) })
  const [create, setCreate] = useState(false)
  const [secret, setSecret] = useState('')
  const [revoke, setRevoke] = useState<Token | null>(null)
  const [busy, setBusy] = useState(false)
  async function remove() {
    if (!revoke) return
    setBusy(true)
    try { await accountRequest(`/account/mcp/tokens/${revoke.id}`, 'DELETE'); setRevoke(null); void qc.invalidateQueries({ queryKey: key }); toast.success('令牌已撤销') }
    catch (error) { toast.error((error as Error).message) }
    finally { setBusy(false) }
  }
  const data = query.data
  return <Card className="space-y-4 p-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-medium"><SettingsHelp title="外部 Agent 接入">将授权知识作为来源，交给你的 Agent 使用。</SettingsHelp></h3></div><Button size="sm" disabled={!data?.enabled || !data.can_use} onClick={() => setCreate(true)}>创建令牌</Button></div>
    {query.isError && <ErrorLine error={query.error} retry={() => void query.refetch()} />}
    {query.isPending && <p role="status" className="text-sm text-muted-foreground">正在加载…</p>}
    {data && <>
      <p role="status" className="rounded-lg border bg-muted/20 p-3 text-sm">{!data.can_use ? '当前角色没有 MCP 接入权限，请联系管理员分配「外部接入」权限。' : !data.enabled ? '系统 MCP 接入已关闭，已有令牌暂不可用，仍可撤销。' : 'MCP 接入已开启 · 只读查询 · 不使用你的聊天 API'}</p>
      <div className="divide-y rounded-lg border">{data.items.length ? data.items.map(token => <div key={token.id} className="flex items-start justify-between gap-3 p-3"><div className="min-w-0 space-y-1"><p className="break-all text-sm font-medium">{token.name}<span className="ml-2 text-xs font-normal text-muted-foreground">{state(token)}</span></p><p className="break-all font-mono text-xs text-muted-foreground">{token.prefix}…</p><p className="break-words text-xs text-muted-foreground">{token.kb_ids.map(id => data.knowledge_bases.find(kb => kb.id === id)?.name ?? '不可访问的知识库').join('、')}</p><p className="text-xs text-muted-foreground">到期 {date(token.expires_at)} · 上次使用 {date(token.last_used_at)}</p></div>{!token.revoked_at && <Button size="sm" variant="outline" onClick={() => setRevoke(token)}>撤销</Button>}</div>) : <p className="p-4 text-sm text-muted-foreground">暂无接入令牌</p>}</div>
      <details className="rounded-lg border p-3"><summary className="cursor-pointer text-sm font-medium">客户端配置示例</summary><div className="mt-4"><ConnectionExamples url={data.public_url} /></div></details>
    </>}
    {create && data && <CreateToken data={data} onClose={() => setCreate(false)} onCreated={value => { setCreate(false); setSecret(value); void qc.invalidateQueries({ queryKey: key }) }} />}
    {secret && <ManagementDialog title="令牌已创建 · 仅显示一次" className="max-w-xl" onClose={() => setSecret('')}><p className="text-sm text-muted-foreground">请现在复制并保存。关闭后无法找回；丢失时撤销旧令牌并重新创建。</p><CodeSample title="访问令牌" value={secret} />{data && <ConnectionExamples url={data.public_url} />}<div className="flex justify-end"><Button onClick={() => setSecret('')}>已保存，关闭</Button></div></ManagementDialog>}
    {revoke && <ConfirmDialog title="撤销 MCP 令牌" message={`撤销「${revoke.name}」后，使用它的 Agent 将无法继续查询。已有客户端取得的内容不会被远程删除。`} danger loading={busy} onConfirm={() => void remove()} onCancel={() => setRevoke(null)} />}
  </Card>
}

const limits: { key: Exclude<keyof Configuration, 'enabled' | 'public_url' | 'rerank_enabled'>; label: string; min: number; max?: number; step?: number; hint: string }[] = [
  { key: 'timeout_seconds', label: '单次查询时长（秒）', min: 5, max: 120, hint: '超时后停止后续处理；底层读取退出前保留并发槽。' },
  { key: 'max_concurrent', label: 'MCP 最大并发', min: 1, hint: '所有 MCP 用户共享的工具执行额度；可自填正整数，无固定上限。默认 1，满额时提示重试。' },
  { key: 'requests_per_minute', label: '每账号每分钟调用数', min: 1, max: 120, hint: '同一账号的多个令牌共用额度。' },
  { key: 'max_output_chars', label: '单次输出字符上限', min: 16000, max: 64000, hint: '完整响应上限，超限时分页或分段返回。' },
  { key: 'rerank_candidates', label: '相关性评估候选数', min: 4, max: 40, hint: '候选越多覆盖越广，但本地计算时间更长。' },
  { key: 'rerank_seconds', label: '相关性评估时间（秒）', min: 2, max: 30, hint: '批次间检查时间；不能中断正在执行的本地推理。' },
  { key: 'rerank_min_score', label: '最低相关性评分', min: 0, max: 1, step: 0.01, hint: '不是正确概率；提高可减少无关结果，也可能漏掉资料。' },
  { key: 'reference_minutes', label: '查询引用有效期（分钟）', min: 5, max: 120, hint: '仅用于阅读入口与分页，失效后重新搜索。' },
]

export function SystemMcpSettings() {
  const { user } = useAuth()
  return <SystemMcpContent key={`${user.id}:${user.permission_revision}`} />
}
function SystemMcpContent() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['mcp-config'], queryFn: ({ signal }) => accountRequest<Configuration>('/admin/mcp/settings', 'GET', undefined, signal) })
  const [draft, setDraft] = useState<Configuration | null>(null)
  const [busy, setBusy] = useState(false)
  const form = draft ?? query.data
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!form) return
    setBusy(true)
    try {
      const saved = await accountRequest<Configuration>('/admin/mcp/settings', 'PUT', form)
      qc.setQueryData(['mcp-config'], saved); setDraft(null)
      void qc.invalidateQueries({ queryKey: ['mcp-personal'] }); toast.success('MCP 配置已保存')
    } catch (error) { toast.error((error as Error).message) }
    finally { setBusy(false) }
  }
  return <>
    <Card className="space-y-4 p-5"><h3 className="font-medium"><SettingsHelp title="独立知识接入服务">为外部 Agent 提供只读知识查询，不创建会话、不生成回答。需要单独部署 MCP 网关；这里的开关控制访问权限，不会启动或停止网关进程。</SettingsHelp></h3>
      {query.isError && <ErrorLine error={query.error} retry={() => void query.refetch()} />}
      {query.isPending && <p role="status" className="text-sm text-muted-foreground">正在加载…</p>}
      {form && <form className="space-y-4" onSubmit={event => void save(event)}><fieldset disabled={busy || !can('system.edit')} className="space-y-4">
        <div className="flex items-center justify-between gap-4"><div><p className="text-sm font-medium">允许 MCP 接入</p><p role="status" className="mt-1 text-xs text-muted-foreground">{draft ? '有未保存更改 · 保存后生效' : query.data?.enabled ? '当前已开启' : '当前已关闭（默认）'}</p></div><Toggle label="允许 MCP 接入，保存后生效" checked={form.enabled} onChange={enabled => setDraft({ ...form, enabled })} /></div>
        <label className="block space-y-1 text-sm"><span><SettingsHelp title="MCP 接入地址">支持 HTTP / HTTPS，客户端使用填写的协议和地址；此处仅用于接入示例，监听地址需在网关部署中配置。</SettingsHelp></span><input type="url" className={accountInput} value={form.public_url} maxLength={500} placeholder="http://knowledge.example.com/mcp" onChange={event => setDraft({ ...form, public_url: event.target.value })} /></label>
        <div className="flex items-center justify-between gap-4 border-t pt-3"><div><p className="text-sm font-medium"><SettingsHelp title="MCP 相关性筛选">使用已部署的本地重排模型，不自动下载。资料不足时可少返回；模型不可用时明确提示降级。仅影响 MCP 概念检索。</SettingsHelp></p></div><Toggle label="MCP 相关性筛选，保存后生效" checked={form.rerank_enabled ?? true} onChange={rerank_enabled => setDraft({ ...form, rerank_enabled })} /></div>
        <div className="grid gap-4 sm:grid-cols-2">{limits.map(item => <label key={item.key} className="space-y-1 text-sm"><span><SettingsHelp title={item.label} label={`${item.label}说明`}>{item.hint}</SettingsHelp></span><input type="number" className={accountInput} value={form[item.key]} required min={item.min} max={item.max} step={item.step ?? 1} onChange={event => setDraft({ ...form, [item.key]: Number(event.target.value) })} />{item.key === 'max_concurrent' && <span className="flex flex-wrap gap-1 pt-1">{[1, 4, 8, 16].map(value => <Button key={value} type="button" size="sm" variant={form.max_concurrent === value ? 'secondary' : 'outline'} aria-pressed={form.max_concurrent === value} aria-label={`MCP 最大并发设为 ${value}`} className="h-7 px-3 text-xs" onClick={() => setDraft({ ...form, max_concurrent: value })}>{value}</Button>)}</span>}</label>)}</div>
        <div className="flex items-center justify-between gap-3 border-t pt-3"><Link className="text-xs text-primary underline" to="/settings?section=mcp">个人令牌与接入示例 →</Link><Button type="submit" disabled={!draft}>{busy ? '正在保存…' : '保存 MCP 配置'}</Button></div>
      </fieldset></form>}
    </Card>
    <McpRecords />
  </>
}

function McpRecords() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'tokens' | 'calls'>('tokens')
  const [page, setPage] = useState(0)
  const [userDraft, setUserDraft] = useState('')
  const [userSearch, setUserSearch] = useState('')
  const [revoke, setRevoke] = useState<Token | null>(null)
  const [busy, setBusy] = useState(false)
  const search = tab === 'calls' ? userSearch : ''
  const params = new URLSearchParams({ offset: String(page * 20), limit: '20', ...(search ? { search } : {}) })
  const query = useQuery({ queryKey: ['mcp-records', tab, page, search], queryFn: ({ signal }) => accountRequest<{ items: (Token | Call)[]; total: number }>(`/admin/mcp/${tab}?${params}`, 'GET', undefined, signal) })
  async function remove() {
    if (!revoke) return
    setBusy(true)
    try { await accountRequest(`/admin/mcp/tokens/${revoke.id}`, 'DELETE'); setRevoke(null); void qc.invalidateQueries({ queryKey: ['mcp-records'] }); void qc.invalidateQueries({ queryKey: ['mcp-personal'] }); toast.success('令牌已撤销') }
    catch (error) { toast.error((error as Error).message) }
    finally { setBusy(false) }
  }
  return <Card className="space-y-4 p-5"><div className="flex items-center justify-between gap-3"><div className="flex min-w-0 items-center gap-2" role="group" aria-label="MCP 记录类型">{(['tokens', 'calls'] as const).map(value => <Button size="sm" key={value} variant={tab === value ? 'secondary' : 'ghost'} aria-pressed={tab === value} onClick={() => { setTab(value); setPage(0) }}>{value === 'tokens' ? '令牌管理' : '调用记录'}</Button>)}<SettingsHelp label="MCP 记录说明">{tab === 'tokens' ? '仅显示令牌摘要，可撤销任意用户的接入；不提供明文令牌。' : '保留最近 30 天、最多 10,000 条完成处理的工具调用；不记录查询内容、文档正文或令牌明文。'}</SettingsHelp></div><Button size="icon" variant="ghost" aria-label="刷新 MCP 记录" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw className={`h-4 w-4 ${query.isFetching ? 'animate-spin' : ''}`} /></Button></div>
    {tab === 'calls' && <form className="flex flex-wrap items-center gap-2" role="search" aria-label="筛选 MCP 调用用户" onSubmit={event => { event.preventDefault(); setUserSearch(userDraft.trim()); setPage(0) }}>
      <input aria-label="筛选 MCP 调用用户" placeholder="用户名或显示名称" maxLength={200} value={userDraft} onChange={event => setUserDraft(event.target.value)} className={`${accountInput} min-w-0 flex-1 sm:max-w-xs`} />
      <Button type="submit" size="sm" variant="outline">筛选</Button>
      {(userDraft || userSearch) && <Button type="button" size="sm" variant="ghost" onClick={() => { setUserDraft(''); setUserSearch(''); setPage(0) }}>重置</Button>}
    </form>}
    {query.isError && <ErrorLine error={query.error} retry={() => void query.refetch()} />}
    {query.isPending ? <p role="status" className="text-sm text-muted-foreground">正在加载…</p> : <div className="overflow-x-auto rounded-lg border"><table className="w-full text-left text-xs"><thead className="bg-muted/30"><tr>{(tab === 'tokens' ? ['用户 / 名称', '状态', '有效期 / 上次使用', '操作'] : ['用户 / 令牌', '工具', '结果 / 耗时', '时间']).map(label => <th key={label} className="whitespace-nowrap px-3 py-2 font-medium">{label}</th>)}</tr></thead><tbody className="divide-y">{query.data?.items.map(item => tab === 'tokens' ? <tr key={item.id}><td className="max-w-48 break-all p-3">{item.username ?? '已删除用户'}<p className="mt-1 text-muted-foreground">{(item as Token).name}</p></td><td className="p-3">{state(item as Token)}</td><td className="whitespace-nowrap p-3">{date((item as Token).expires_at)}<p className="mt-1 text-muted-foreground">{date((item as Token).last_used_at)}</p></td><td className="p-3"><Button size="sm" variant="outline" disabled={!can('system.edit') || !!(item as Token).revoked_at} onClick={() => setRevoke(item as Token)}>撤销</Button></td></tr> : <tr key={item.id}><td className="max-w-40 break-all p-3">{(item as Call).display_name || item.username || '已删除用户'}{(item as Call).display_name && <p className="text-muted-foreground">{item.username}</p>}<p className="mt-1 text-muted-foreground">{(item as Call).token_name ?? '—'}</p></td><td className="max-w-48 break-all p-3 font-mono">{(item as Call).tool}</td><td className="whitespace-nowrap p-3">{callState((item as Call).status)}<p className="mt-1 text-muted-foreground">{((item as Call).duration_ms / 1000).toFixed(2)} 秒</p></td><td className="whitespace-nowrap p-3">{date(item.created_at)}</td></tr>)}{!query.data?.items.length && <tr><td colSpan={4} className="p-5 text-center text-muted-foreground">{search ? '未找到匹配用户的调用记录' : '暂无记录'}</td></tr>}</tbody></table></div>}
    <div className="flex items-center justify-between text-xs text-muted-foreground"><span>共 {query.data?.total ?? 0} 条 · 每页 20 条</span><div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={!page || query.isFetching} onClick={() => setPage(page - 1)}>上一页</Button><span>{page + 1}</span><Button size="sm" variant="outline" disabled={query.isFetching || (page + 1) * 20 >= (query.data?.total ?? 0)} onClick={() => setPage(page + 1)}>下一页</Button></div></div>
    {revoke && <ConfirmDialog title="撤销 MCP 令牌" message={`确认撤销 ${revoke.username ?? '已删除用户'} 的「${revoke.name}」？使用该令牌的 Agent 将无法继续查询。`} danger loading={busy} onConfirm={() => void remove()} onCancel={() => setRevoke(null)} />}
  </Card>
}
