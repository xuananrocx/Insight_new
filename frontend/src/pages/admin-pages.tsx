import { useConfirm } from '@/components/confirmation-provider'
import { OptionSelect } from '@/components/ui/select'
import { useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ManagementDialog } from '@/components/management-dialog'
import { Toggle } from '@/components/ui/toggle-switch'
import { AccessEditor, PreviewPanel, actionNames, type Preview } from '@/components/access-editor'
import { accountInput, useAuth } from '@/hooks/use-auth'
import { accountApi, accountRequest, type Account } from '@/lib/account-api'
import { ProviderSettings } from '@/pages/account-page'

type Role = { id: string; name: string; description: string; permissions: string[]; enabled: boolean; protected: boolean; users_count: number }
type Capability = { key: string; group: string; label: string; requires?: string }
type Resource = { id: string; name: string; kind: 'kb' | 'api'; scope: string; enabled: boolean; owner?: { id: string; username: string } }
const error = (e: unknown) => toast.error((e as Error).message)
function Page({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <div className="mx-auto max-w-6xl space-y-5 p-4 md:p-8"><div><p className="mb-1 text-xs text-muted-foreground">系统管理</p><h1 className="text-2xl font-semibold">{title}</h1><p className="mt-2 text-sm text-muted-foreground">{description}</p></div>{children}</div>
}
function RoleChoices({ roles, ids, onChange, disabled = false }: { roles: Role[]; ids: string[]; onChange: (ids: string[]) => void; disabled?: boolean }) {
  const { user } = useAuth()
  return <div className="flex flex-wrap gap-4">{roles.map(r => <label className="flex items-center gap-2 text-sm" key={r.id}><span>{r.name}{!r.enabled && '（停用）'}</span><Toggle label={`分配${r.name}`} checked={ids.includes(r.id)} disabled={disabled || !r.enabled || (r.protected && !user.is_super) || !r.permissions.every(p => user.permissions.includes(p))} onChange={on => onChange(on ? [...ids, r.id] : ids.filter(id => id !== r.id))} /></label>)}</div>
}

export function UsersPage() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ['admin-users'], queryFn: accountApi.users })
  const roles = useQuery({ queryKey: ['admin-roles'], queryFn: () => accountRequest<Role[]>('/admin/roles'), enabled: can('users.roles') || can('users.create') })
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const [roleFilter, setRoleFilter] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', display_name: '', note: '', role_ids: ['member'] })
  const filtered = users.data?.filter(u => `${u.username} ${u.display_name || ''}`.toLowerCase().includes(search.toLowerCase()) && (status === 'all' || !!u.disabled === (status === 'disabled')) && (!roleFilter || u.roles.some(r => r.id === roleFilter)))
  const roleOptions = Array.from(new Map(users.data?.flatMap(u => u.roles.map(r => [r.id, r] as const)) || []).values())
  async function create() {
    setBusy(true)
    try { await accountRequest('/admin/users', 'POST', form); setCreating(false); setForm({ username: '', password: '', display_name: '', note: '', role_ids: ['member'] }); await qc.invalidateQueries(); toast.success('账号已创建，首次登录需修改密码') } catch (e) { error(e) } finally { setBusy(false) }
  }
  const target = users.data?.find(u => u.id === selected)
  return <Page title="用户管理" description="注册已关闭，由有权限的管理员创建账号。多角色权限合并；停用账号会立即撤销登录会话。">
    <div className="flex flex-wrap gap-3"><input aria-label="搜索用户" className={`${accountInput} max-w-xs`} placeholder="搜索用户名或显示名称" value={search} onChange={e => setSearch(e.target.value)} /><OptionSelect aria-label="筛选账号状态" className={`${accountInput} sm:w-36`} value={status} onValueChange={setStatus} options={[{ value: 'all', label: '全部状态' }, { value: 'enabled', label: '启用' }, { value: 'disabled', label: '停用' }]} /><OptionSelect aria-label="筛选角色" className={`${accountInput} sm:w-44`} value={roleFilter} onValueChange={setRoleFilter} options={[{ value: '', label: '全部角色' }, ...roleOptions.map(r => ({ value: r.id, label: r.name }))]} />{can('users.create') && <Button onClick={() => setCreating(!creating)}>创建账号</Button>}</div>
    {creating && <ManagementDialog title="创建账号" busy={busy} onClose={() => { setCreating(false); setForm({ username: '', password: '', display_name: '', note: '', role_ids: ['member'] }) }}><form className="space-y-4" onSubmit={e => { e.preventDefault(); void create() }}><div className="grid gap-3 sm:grid-cols-2"><label className="text-sm">用户名<input className={accountInput} required minLength={2} maxLength={64} pattern="[a-zA-Z0-9_.@\-]+" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></label><label className="text-sm">初始密码<input className={accountInput} required minLength={12} maxLength={128} type="password" autoComplete="new-password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label><label className="text-sm">显示名称<input className={accountInput} maxLength={80} value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} /></label><label className="text-sm">备注<input className={accountInput} maxLength={500} value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} /></label></div><RoleChoices roles={roles.data || []} ids={form.role_ids} disabled={!can('users.roles')} onChange={ids => setForm({ ...form, role_ids: ids })} /><div className="flex gap-2"><Button type="submit" disabled={busy}>创建</Button><Button type="button" variant="outline" onClick={() => { setCreating(false); setForm({ ...form, password: '' }) }}>取消</Button></div></form></ManagementDialog>}
    {users.error && <p className="text-destructive">{users.error.message}</p>}
    <Card className="divide-y">{filtered?.map(u => <button key={u.id} onClick={() => setSelected(selected === u.id ? null : u.id)} className={`flex w-full flex-wrap items-center justify-between gap-3 p-4 text-left hover:bg-muted/40 ${selected === u.id ? 'bg-muted/50' : ''}`}><div><p className="font-medium">{u.display_name || u.username}<span className="ml-2 text-sm text-muted-foreground">{u.display_name ? u.username : ''}</span></p><p className="mt-1 text-xs text-muted-foreground">{u.roles.map(r => `${r.name}${r.enabled ? '' : '（停用）'}`).join('、') || '无角色'}</p></div><div className="text-right text-xs"><p>{u.disabled ? '已停用' : '启用中'}{u.must_change_password ? ' · 待修改密码' : ''}</p><p className="mt-1 text-muted-foreground">最近登录：{u.last_login ? new Date(u.last_login * 1000).toLocaleString() : '尚未登录'}</p></div></button>)}{filtered?.length === 0 && <p className="p-5 text-muted-foreground">没有匹配账号。</p>}</Card>
    {target && <UserDetails key={target.id+target.permission_revision} target={target} roles={roles.data || []} onClose={() => setSelected(null)} />}
  </Page>
}

function UserDetails({ target, roles, onClose }: { target: Account; roles: Role[]; onClose: () => void }) {
  const confirm = useConfirm()
  const { user, can } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState('profile')
  const [name, setName] = useState(target.display_name || '')
  const [note, setNote] = useState(target.note || '')
  const [ids, setIds] = useState(target.roles.map(r => r.id))
  const [password, setPassword] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [pending, setPending] = useState<Record<string, unknown>>({})
  const [busy, setBusy] = useState(false)
  const resources = useQuery({ queryKey: ['user-resources', target.id], queryFn: () => accountRequest<(Resource & { actions: string[]; reason: string; sources: { source: string }[] })[]>(`/admin/users/${target.id}/resources`), enabled: tab === 'resources' })
  const sessions = useQuery({ queryKey: ['user-sessions', target.id], queryFn: () => accountRequest<{ expires_at: number }[]>(`/admin/users/${target.id}/sessions`), enabled: tab === 'sessions' && can('users.sessions') })
  const manageable = (!target.is_super || user.is_super) && target.permissions.every(p => user.permissions.includes(p))
  const other = target.id !== user.id
  async function prepare(body: Record<string, unknown>) {
    setBusy(true); setPending(body); setPreview(null)
    try { setPreview(await accountRequest(`/admin/users/${target.id}/preview`, 'POST', body)) } catch (e) { error(e) } finally { setBusy(false) }
  }
  async function save() {
    setBusy(true)
    try { await accountRequest(`/admin/users/${target.id}`, 'PATCH', { ...pending, expected_revision: preview!.revision }); setPreview(null); setPassword(''); await qc.invalidateQueries(); window.dispatchEvent(new Event('insight-permissions-changed')); toast.success('账号已更新'); onClose() } catch (e) { setPreview(null); error(e) } finally { setBusy(false) }
  }
  return <ManagementDialog title={`${target.username} · 账号详情`} busy={busy} onClose={onClose}><div className="flex flex-wrap gap-2">{[['profile', '基本资料'], ['roles', '角色权限'], ['resources', '资源权限'], ...(can('users.sessions') && manageable ? [['sessions', '登录会话']] : [])].map(([value, label]) => <Button key={value} size="sm" variant={tab === value ? 'default' : 'outline'} onClick={() => { setTab(value); setPreview(null) }}>{label}</Button>)}</div>
    {tab === 'profile' && <div className="space-y-4"><div className="grid gap-3 sm:grid-cols-2"><label className="text-sm">显示名称<input className={accountInput} value={name} maxLength={80} disabled={!can('users.edit') || !manageable} onChange={e => { setName(e.target.value); setPreview(null) }} /></label><label className="text-sm">备注<input className={accountInput} value={note} maxLength={500} disabled={!can('users.edit') || !manageable} onChange={e => { setNote(e.target.value); setPreview(null) }} /></label></div>{can('users.edit') && manageable && <Button disabled={busy} onClick={() => prepare({ display_name: name, note })}>预览保存资料</Button>}<div className="flex items-center gap-3 text-sm"><span>启用账号</span><Toggle label={`启用 ${target.username}`} checked={!target.disabled} disabled={!can('users.disable') || !manageable || !other || busy} onChange={on => prepare({ disabled: !on })} /></div>{can('users.password') && manageable && other && <form className="flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); void prepare({ password }) }}><input aria-label="重置为初始密码" placeholder="新的初始密码（至少 12 位）" className={`${accountInput} max-w-md`} type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={password} onChange={e => { setPassword(e.target.value); setPreview(null) }} /><Button type="submit" disabled={busy} variant="outline">预览重置密码</Button><p className="w-full text-xs text-muted-foreground">重置会使现有登录失效，用户下次登录必须更换密码，操作记入审计。</p></form>}</div>}
    {tab === 'roles' && <div className="space-y-4"><p className="text-sm text-muted-foreground">角色权限取并集，停用角色不生效。</p>{roles.length ? <RoleChoices roles={roles} ids={ids} disabled={!can('users.roles') || !manageable || !other} onChange={value => { setIds(value); setPreview(null) }} /> : <p>{target.roles.map(r => r.name).join('、')}</p>}{can('users.roles') && manageable && other && <Button disabled={busy} onClick={() => prepare({ role_ids: ids })}>预览角色变更</Button>}</div>}
    {tab === 'resources' && <div className="space-y-3"><p className="text-xs text-muted-foreground">仅展示你有权管理的资源，不显示该用户的私有知识库、个人 API 或聊天。</p>{resources.error && <p className="text-destructive">{resources.error.message}</p>}{resources.data?.map(r => <div className="rounded border p-3 text-sm" key={r.kind+r.id}><p>{r.name} · {r.kind === 'kb' ? '知识库' : '团队 API'}</p><p>最终权限：{r.actions.map(a => actionNames[a]).join('、') || '无'}</p><p className="text-xs text-muted-foreground">{r.reason || r.sources.map(s => s.source).join('、')}</p></div>)}</div>}
    {tab === 'sessions' && <div className="space-y-3"><p className="text-sm">当前有效登录：{sessions.data?.length ?? '…'} 个</p>{sessions.error && <p className="text-destructive">{sessions.error.message}</p>}{sessions.data?.map((s, i) => <p className="text-xs text-muted-foreground" key={i}>到期时间：{new Date(s.expires_at * 1000).toLocaleString()}</p>)}{other && <Button variant="outline" disabled={busy} onClick={async () => { if (!await confirm(`让 ${target.username} 的全部登录会话退出？`)) return; setBusy(true); try { await accountRequest(`/admin/users/${target.id}/sessions`, 'DELETE'); await sessions.refetch(); toast.success('登录会话已撤销') } catch (e) { error(e) } finally { setBusy(false) } }}>强制全部退出</Button>}</div>}
    {preview && <PreviewPanel value={preview} busy={busy} onSave={save} onCancel={() => { setPreview(null); setPending({}) }} />}
  </ManagementDialog>
}

export function RolesPage() {
  const confirm = useConfirm()
  const { can, user } = useAuth()
  const qc = useQueryClient()
  const roles = useQuery({ queryKey: ['admin-roles'], queryFn: () => accountRequest<Role[]>('/admin/roles') })
  const catalog = useQuery({ queryKey: ['permission-catalog'], queryFn: () => accountRequest<{ items: Capability[]; revision: number }>('/permissions/catalog') })
  const [form, setForm] = useState<Role | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [membersOf, setMembersOf] = useState('')
  const members = useQuery({ queryKey: ['role-members', membersOf], queryFn: () => accountRequest<{ id: string; username: string; disabled: boolean }[]>(`/admin/roles/${membersOf}/members`), enabled: !!membersOf })
  function patch(value: Partial<Role>) { setForm(form ? { ...form, ...value } : null); setPreview(null) }
  async function save() {
    if (!form) return
    setBusy(true)
    try { await accountRequest(`/admin/roles${form.id ? '/'+form.id : ''}`, form.id ? 'PUT' : 'POST', { ...form, expected_revision: preview?.revision ?? catalog.data?.revision }); setForm(null); setPreview(null); await qc.invalidateQueries(); window.dispatchEvent(new Event('insight-permissions-changed')); toast.success('角色已保存') } catch (e) { setPreview(null); error(e) } finally { setBusy(false) }
  }
  return <Page title="角色管理" description="自定义角色并为账号分配多个角色。功能权限与具体知识库、API 的授权分开设置；增强AI和深度AI 不设角色门槛。">
    {can('roles.manage') && <Button onClick={() => { setPreview(null); setForm({ id: '', name: '', description: '', permissions: [], enabled: true, protected: false, users_count: 0 }) }}>创建角色</Button>}
    {roles.error && <p className="text-destructive">{roles.error.message}</p>}
    <div className="grid gap-4 md:grid-cols-2">{roles.data?.map(r => <Card className="space-y-3 p-5" key={r.id}><div className="flex items-center justify-between gap-3"><h2 className="font-semibold">{r.name}</h2><span className="text-xs text-muted-foreground">{r.protected ? '受保护' : r.enabled ? '启用' : '停用'} · {r.users_count} 人</span></div><p className="text-sm text-muted-foreground">{r.description || `${r.permissions.length} 项功能权限`}</p><div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => { setForm(r); setPreview(null) }}>{can('roles.manage') && !r.protected ? '查看 / 编辑' : '查看权限'}</Button><Button size="sm" variant="ghost" onClick={() => setMembersOf(membersOf === r.id ? '' : r.id)}>查看成员</Button>{can('roles.manage') && r.permissions.every(p => user.permissions.includes(p)) && <Button size="sm" variant="ghost" onClick={() => { setPreview(null); setForm({ ...r, id: '', name: r.name+' 副本', protected: false, users_count: 0 }) }}>复制</Button>}{can('roles.manage') && !r.protected && r.users_count === 0 && <Button size="sm" variant="ghost" onClick={async () => { if (!await confirm(`删除角色“${r.name}”？仍有关联资源授权时无法删除。`)) return; try { await accountRequest(`/admin/roles/${r.id}`, 'DELETE'); await qc.invalidateQueries(); toast.success('角色已删除') } catch (e) { error(e) } }}>删除</Button>}</div></Card>)}</div>
    {membersOf && <ManagementDialog title={`${roles.data?.find(r => r.id === membersOf)?.name || '角色'} · 成员`} onClose={() => setMembersOf('')}><div className="space-y-3 text-sm">{members.isLoading && <p>加载中…</p>}{members.error && <p className="text-destructive">{members.error.message}</p>}{members.data?.map(m => <p key={m.id}>{m.username}{m.disabled ? '（停用）' : ''}</p>)}{members.data?.length === 0 && <p>暂无成员。</p>}</div></ManagementDialog>}
    {form && <ManagementDialog title={form.id ? form.name : '新角色'} busy={busy} onClose={() => { setForm(null); setPreview(null) }}><fieldset className="space-y-4" disabled={busy || !can('roles.manage') || !!form.protected || !form.permissions.every(p => user.permissions.includes(p))}><label className="block text-sm">角色名称<input className={accountInput} maxLength={60} value={form.name} onChange={e => patch({ name: e.target.value })} /></label><label className="block text-sm">说明<input className={accountInput} maxLength={500} value={form.description} onChange={e => patch({ description: e.target.value })} /></label><label className="flex items-center gap-3 text-sm">启用角色<Toggle label="启用角色" checked={!!form.enabled} onChange={enabled => patch({ enabled })} /></label>{Array.from(new Set(catalog.data?.items.map(c => c.group))).map(group => <div className="space-y-3 rounded border p-4" key={group}><h3 className="text-sm font-medium">{group}</h3><div className="grid gap-3 md:grid-cols-2">{catalog.data?.items.filter(c => c.group === group).map(c => <label className="flex items-center justify-between gap-3 text-sm" key={c.key}><span>{c.label}</span><Toggle label={c.label} checked={form.permissions.includes(c.key)} disabled={!user.permissions.includes(c.key)} onChange={on => {
      let caps = on ? [...form.permissions, c.key, ...(c.requires ? [c.requires] : [])] : form.permissions.filter(p => p !== c.key && !catalog.data?.items.some(x => x.key === p && x.requires === c.key))
      patch({ permissions: [...new Set(caps)] })
    }} /></label>)}</div></div>)}</fieldset><div className="flex gap-2">{can('roles.manage') && !form.protected && <Button disabled={busy || !form.name.trim()} onClick={async () => { if (!form.id) { await save(); return } setBusy(true); try { setPreview(await accountRequest(`/admin/roles/${form.id}/preview`, 'POST', form)) } catch (e) { error(e) } finally { setBusy(false) } }}>{form.id ? '预览变更' : '创建角色'}</Button>}<Button variant="outline" onClick={() => { setForm(null); setPreview(null) }}>关闭</Button></div>{preview && <PreviewPanel value={preview} onSave={save} onCancel={() => setPreview(null)} busy={busy} />}</ManagementDialog>}
  </Page>
}

export function ResourcesPage() {
  const { can } = useAuth()
  const resources = useQuery({ queryKey: ['access-resources'], queryFn: () => accountRequest<Resource[]>('/access/resources') })
  const [selected, setSelected] = useState('')
  const [search, setSearch] = useState('')
  const [teamName, setTeamName] = useState('')
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  return <Page title="知识库授权" description="管理你拥有或获准管理的知识库。团队管理权限只提供归属和授权管理，不会自动开放知识内容。">
    <input className={`${accountInput} max-w-sm`} aria-label="搜索知识库授权" value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索知识库" />
    {can('kb.team_create') && <Button onClick={() => setCreating(true)}>创建团队知识库</Button>}
    {creating && <ManagementDialog title="创建团队知识库" busy={busy} onClose={() => { setCreating(false); setTeamName('') }}><form className="space-y-4" onSubmit={async e => { e.preventDefault(); setBusy(true); try { await accountRequest('/kbs', 'POST', { name: teamName, scope: 'team' }); setTeamName(''); setCreating(false); await resources.refetch(); toast.success('团队知识库已创建') } catch (err) { error(err) } finally { setBusy(false) } }}><input className={`${accountInput} max-w-sm`} aria-label="团队知识库名称" placeholder="团队知识库名称" required maxLength={100} value={teamName} onChange={e => setTeamName(e.target.value)} /><div className="flex gap-2"><Button disabled={busy} type="submit">创建团队知识库</Button><Button type="button" disabled={busy} variant="outline" onClick={() => { setCreating(false); setTeamName('') }}>取消</Button></div></form></ManagementDialog>}
    {resources.error && <p className="text-destructive">{resources.error.message}</p>}{resources.data?.filter(r => r.kind === 'kb' && r.name.toLowerCase().includes(search.toLowerCase())).map(r => <Card className="space-y-3 p-5" key={r.id}><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-medium">{r.name}</h2><p className="text-xs text-muted-foreground">{r.scope === 'team' ? '团队' : '私有'} · 所有者 {r.owner?.username || '未分配'} · {r.enabled ? '启用' : '停用'}</p></div><Button variant="outline" onClick={() => setSelected(selected === r.id ? '' : r.id)}>管理授权</Button></div>{selected === r.id && <ManagementDialog title={`${r.name} · 管理授权`} onClose={() => setSelected('')}><ResourcePolicy resource={r} /><AccessEditor key={r.id} kind="kb" id={r.id} /></ManagementDialog>}</Card>)}{resources.data?.filter(r => r.kind === 'kb').length === 0 && <Card className="p-5 text-muted-foreground">暂无可管理的知识库。</Card>}
  </Page>
}

function ResourcePolicy({ resource }: { resource: Resource }) {
  const confirm = useConfirm()
  const { user, can } = useAuth()
  const qc = useQueryClient()
  const members = useQuery({ queryKey: ['directory'], queryFn: accountApi.members })
  const [owner, setOwner] = useState('')
  const [busy, setBusy] = useState(false)
  const allowed = resource.owner?.id === user.id || (resource.scope === 'team' && can('kb.team_manage'))
  async function save(body: Record<string, unknown>, message: string) {
    if (!await confirm(message)) return
    setBusy(true)
    try { const rev = await accountRequest<{ revision: number }>('/permissions/catalog'); await accountRequest(`/access/kb/${resource.id}/policy`, 'PUT', { ...body, expected_revision: rev.revision }); await qc.invalidateQueries(); window.dispatchEvent(new Event('insight-permissions-changed')); toast.success('知识库属性已更新') } catch (e) { error(e) } finally { setBusy(false) }
  }
  if (!allowed) return null
  return <div className="space-y-3 rounded border p-3"><label className="flex items-center gap-3 text-sm">启用知识库<Toggle label={`启用知识库 ${resource.name}`} checked={!!resource.enabled} disabled={busy} onChange={enabled => save({ enabled }, enabled ? '重新启用这个知识库？' : '停用后所有人将无法继续使用此知识库，历史会话保留。继续？')} /></label><div className="flex flex-wrap gap-2"><OptionSelect className={`${accountInput} max-w-xs`} aria-label="转移知识库所有者" value={owner} onValueChange={setOwner} disabled={busy} options={[{ value: '', label: '选择新的所有者' }, ...members.data?.filter(u => u.id !== resource.owner?.id).map(u => ({ value: u.id, label: u.username })) || []]} /><Button variant="outline" disabled={!owner || busy} onClick={() => save({ owner_id: owner }, '转移后原所有者不再自动拥有权限，现有共享授权保留。继续？')}>转移归属</Button>{resource.owner?.id === user.id && (resource.scope === 'team' || can('kb.team_create')) && <Button variant="outline" disabled={busy} onClick={() => save({ scope: resource.scope === 'team' ? 'private' : 'team' }, resource.scope === 'team' ? '转为私有知识库？需先移除所有共享授权。' : '转为团队知识库？团队资源管理者将能够管理其归属和授权。')}>{resource.scope === 'team' ? '转为私有' : '转为团队'}</Button>}</div></div>
}

export function TeamApiPage() {
  const { can } = useAuth()
  const stats = useQuery({ queryKey: ['team-api-stats'], queryFn: () => accountRequest<{ id: string; name: string; calls: number; token_input: number; token_output: number; failures: number }[]>('/admin/api-stats'), enabled: can('api.stats') })
  return <Page title="团队 API" description="配置管理、使用授权和调用统计分别控制。包括管理员在内，调用团队 API 都需要明确授权；服务地址不设域名白名单。"><ProviderSettings team />{can('api.stats') && <Card className="space-y-3 p-5"><h2 className="font-semibold">团队调用统计</h2><p className="text-xs text-muted-foreground">仅显示汇总，不含用户问题和回答。统计基于升级后保留且启用了记录的调用日志；当前调用链暂未采集 Token 用量。</p>{stats.error && <p className="text-destructive">{stats.error.message}</p>}{stats.data?.map(s => <p className="text-sm" key={s.id}>{s.name}：{s.calls} 次调用 · {s.failures} 次失败</p>)}</Card>}</Page>
}

type AuditRecord = { id: number; actor: string | null; username: string | null; action: string; action_label: string; target: string; details: string[]; created_at: number }
const auditActorKey = (record: AuditRecord) => record.actor ? `user:${record.actor}` : 'system'
const auditActorName = (record: AuditRecord) => record.username || (record.actor ? `已删除用户（${record.actor}）` : '系统')

export function AuditPage() {
  const { can } = useAuth()
  const audit = useQuery({ queryKey: ['admin-audit'], queryFn: () => accountRequest<AuditRecord[]>('/admin/audit') })
  const [search, setSearch] = useState('')
  const [selectedActor, setSelectedActor] = useState('')
  const [confirmClear, setConfirmClear] = useState(false)
  const [busy, setBusy] = useState(false)
  const actors = new Map((audit.data || []).map(r => [auditActorKey(r), auditActorName(r)]))
  if (selectedActor && !actors.has(selectedActor)) actors.set(selectedActor, '所选用户（暂无记录）')
  const keyword = search.trim().toLocaleLowerCase()
  const records = audit.data?.filter(r => (!selectedActor || auditActorKey(r) === selectedActor) && `${auditActorName(r)} ${r.action_label} ${r.action} ${r.target} ${r.details.join(' ')}`.toLocaleLowerCase().includes(keyword)) || []
  // Group by stable account ID, keeping each user's newest records first.
  const groups = new Map<string, { name: string; records: AuditRecord[] }>()
  for (const record of records) {
    const key = auditActorKey(record)
    if (!groups.has(key)) groups.set(key, { name: auditActorName(record), records: [] })
    groups.get(key)!.records.push(record)
  }
  async function clear() {
    setBusy(true)
    try {
      await accountRequest('/admin/audit', 'DELETE')
      setConfirmClear(false)
      setSelectedActor('')
      await audit.refetch()
      toast.success('操作日志已清空')
    } catch (e) { error(e) } finally { setBusy(false) }
  }
  return <Page title="操作日志" description="记录账号、角色、资源授权和 API 配置变更，不记录聊天内容或密钥。最近 200 条记录按操作用户分组，可按用户和关键词筛选。">
    <div className="flex flex-wrap gap-2">
      <OptionSelect aria-label="筛选操作用户" className={`${accountInput} sm:w-56`} value={selectedActor} onValueChange={setSelectedActor} options={[{ value: '', label: '全部用户' }, ...Array.from(actors, ([value, label]) => ({ value, label }))]} />
      <input className={`${accountInput} max-w-sm`} placeholder="搜索操作者、操作名称或详情" aria-label="搜索操作日志" value={search} onChange={e => setSearch(e.target.value)} />
      {(selectedActor || search) && <Button variant="ghost" onClick={() => { setSelectedActor(''); setSearch('') }}>重置筛选</Button>}
      <Button variant="outline" disabled={audit.isFetching} onClick={() => audit.refetch()}>刷新</Button>
      {can('audit.clear') && <Button variant="outline" disabled={busy} onClick={() => setConfirmClear(true)}>清空操作日志</Button>}
    </div>
    {audit.error && <p role="alert" className="text-destructive">{audit.error.message}</p>}
    {audit.isLoading ? <p className="p-4 text-sm text-muted-foreground">加载中…</p> : !audit.error && <p className="text-sm text-muted-foreground">{records.length ? `${groups.size} 个分组 · ${records.length} 条记录` : keyword || selectedActor ? '没有匹配的操作日志' : '暂无操作日志'}</p>}
    {Array.from(groups, ([key, group]) => <section key={key} aria-label={`${group.name}的操作日志`}>
      <Card className="overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b bg-muted/40 px-4 py-3"><h2 className="min-w-0 break-all text-sm font-semibold">{group.name}</h2><span className="shrink-0 text-xs text-muted-foreground">{group.records.length} 条记录</span></div>
        <div className="divide-y">{group.records.map(r => <div className="space-y-2 p-4 text-sm" key={r.id}>
          <div className="flex flex-wrap items-center justify-between gap-2"><p className="break-all font-medium">{r.action_label}</p><time className="text-xs text-muted-foreground" dateTime={new Date(r.created_at).toISOString()}>{new Date(r.created_at).toLocaleString('zh-CN', { hour12: false })}</time></div>
          <div className="space-y-1 whitespace-pre-wrap break-words text-xs text-muted-foreground [overflow-wrap:anywhere]">{r.details.map((detail, i) => <p key={i}>{detail}</p>)}</div>
        </div>)}</div>
      </Card>
    </section>)}
    {confirmClear && <ManagementDialog title="清空操作日志" busy={busy} onClose={() => setConfirmClear(false)}>
      <p className="text-sm">将清空全部操作日志，并保留本次清空记录。系统日志不受影响。此操作无法撤销。</p>
      <div className="flex gap-2"><Button variant="destructive" disabled={busy} onClick={clear}>确认清空</Button><Button variant="outline" disabled={busy} onClick={() => setConfirmClear(false)}>取消</Button></div>
    </ManagementDialog>}
  </Page>
}
