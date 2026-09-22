import { OptionSelect } from '@/components/ui/select'
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { ManagementDialog } from '@/components/management-dialog'
import { Toggle } from '@/components/ui/toggle-switch'
import { accountInput } from '@/hooks/use-auth'
import { accountApi, accountRequest } from '@/lib/account-api'

export const actionNames: Record<string, string> = { query: '查询 / 提问', edit: '编辑文档', manage: '管理及授权', export: '导出知识库', download: '下载原文件', use: '调用 API' }
export type Preview = { revision: number; affected_count: number; changes: { username: string; added: string[]; removed: string[]; after?: string[]; reason?: string; disabled?: boolean; sources?: { source: string; effect: string; actions: string[] }[]; resources?: { kind: string; resource_id: string; before: string[]; after: string[] }[] }[] }
export function PreviewPanel({ value, onSave, onCancel, busy = false }: { value: Preview; onSave: () => void; onCancel: () => void; busy?: boolean }) {
  const catalog = useQuery({ queryKey: ['permission-catalog'], queryFn: () => accountRequest<{ items: { key: string; label: string }[] }>('/permissions/catalog') })
  const names = { ...actionNames, ...Object.fromEntries(catalog.data?.items.map(x => [x.key, x.label]) || []) }
  const label = (values: string[]) => values.map(x => names[x] || x).join('、') || '无'
  return <ManagementDialog title="权限变更预览" busy={busy} onClose={onCancel}><div className="space-y-3" role="region" aria-label="权限变更预览">
    <p className="font-medium">变更预览 · 影响 {value.affected_count} 位用户</p>
    <div className="max-h-80 space-y-3 overflow-y-auto break-words text-sm">{value.changes.map((c, i) => <div key={i} className="border-b pb-2 last:border-0"><p className="font-medium">{c.username}</p><p>新增：{label(c.added)}</p><p>撤销：{label(c.removed)}</p>{c.after && <p>保存后仍有：{label(c.after)}</p>}{c.disabled !== undefined && <p>账号状态：{c.disabled ? '停用，登录会话立即失效' : '启用'}</p>}{c.reason && <p>{c.reason}</p>}{c.sources?.map((s, j) => <p key={j} className="text-xs text-muted-foreground">{s.source}：{s.effect === 'deny' ? '明确禁止' : label(s.actions)}</p>)}{c.resources?.map(r => <p key={r.kind+r.resource_id}>关联资源 {r.resource_id}：{label(r.before)} → {label(r.after)}</p>)}</div>)}</div>
    <p className="text-xs text-muted-foreground">各角色与单独授权合并生效；明确禁止优先。历史会话保留。</p>
    <div className="flex gap-2"><Button disabled={busy} onClick={onSave}>确认保存</Button><Button disabled={busy} variant="outline" onClick={onCancel}>取消</Button></div>
  </div></ManagementDialog>
}

type Grant = { subject_type: 'user' | 'role'; subject_id: string; name?: string; effect: 'allow' | 'deny' | 'remove'; actions: string[] }
type Effective = { actions: string[]; reason: string; sources: { source: string; effect: string; actions: string[] }[] }
export function AccessEditor({ kind, id }: { kind: 'kb' | 'api'; id: string }) {
  const qc = useQueryClient()
  const path = `/access/${kind}/${id}`
  const users = useQuery({ queryKey: ['directory'], queryFn: accountApi.members })
  const roles = useQuery({ queryKey: ['role-directory'], queryFn: () => accountRequest<{ id: string; name: string; enabled: boolean }[]>('/roles/directory') })
  const grants = useQuery({ queryKey: ['access-grants', kind, id], queryFn: () => accountRequest<{ items: Grant[]; revision: number }>(path) })
  const [change, setChange] = useState<Grant>({ subject_type: 'user', subject_id: '', effect: 'allow', actions: kind === 'kb' ? ['query'] : ['use'] })
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [inspect, setInspect] = useState('')
  const effective = useQuery({ queryKey: ['effective-access', kind, id, inspect], queryFn: () => accountRequest<Effective>(`${path}/effective/${inspect}`), enabled: !!inspect })
  function update(next: Partial<Grant>) { setChange({ ...change, ...next }); setPreview(null) }
  async function prepare(next = change) {
    setBusy(true); setPreview(null); setChange(next)
    try { setPreview(await accountRequest<Preview>(`${path}/preview`, 'POST', next)) } catch (e) { toast.error((e as Error).message) } finally { setBusy(false) }
  }
  async function save() {
    const revision = preview?.revision ?? grants.data?.revision
    if (busy || !change.subject_id || revision === undefined) return
    setBusy(true)
    try { await accountRequest(path, 'PUT', { ...change, expected_revision: revision }); setPreview(null); await qc.invalidateQueries(); window.dispatchEvent(new Event('insight-permissions-changed')); toast.success('授权已保存') }
    catch (e) { setPreview(null); toast.error((e as Error).message); await grants.refetch() } finally { setBusy(false) }
  }
  return <div className="space-y-4 border-t pt-4">
    <h3 className="font-medium">{kind === 'kb' ? '知识库授权' : 'API 使用授权'}</h3>
    <p className="text-xs text-muted-foreground">默认不共享。可以按用户或角色授权；删除一条授权不会覆盖其他来源，明确禁止可停止该用户访问。</p>
    {grants.error && <p role="alert" className="text-destructive">{grants.error.message}</p>}
    <fieldset disabled={busy} className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-3"><OptionSelect aria-label="授权对象类型" className={accountInput} disabled={busy} value={change.subject_type} onValueChange={value => update({ subject_type: value as 'user' | 'role', subject_id: '', effect: 'allow' })} options={[{ value: 'user', label: '指定用户' }, { value: 'role', label: '指定角色' }]} /><OptionSelect aria-label="授权对象" className={accountInput} disabled={busy} value={change.subject_id} onValueChange={value => update({ subject_id: value })} options={[{ value: '', label: '请选择' }, ...(change.subject_type === 'user' ? users.data?.map(u => ({ value: u.id, label: u.username })) : roles.data?.map(r => ({ value: r.id, label: r.name + (r.enabled ? '' : '（已停用）') }))) || []]} /><OptionSelect aria-label="授权规则" className={accountInput} disabled={busy} value={change.effect} onValueChange={value => update({ effect: value as Grant['effect'] })} options={[{ value: 'allow', label: '允许' }, ...(change.subject_type === 'user' ? [{ value: 'deny', label: '明确禁止访问' }] : []), { value: 'remove', label: '移除此条授权' }]} /></div>
      {change.effect === 'allow' && <div className="flex flex-wrap gap-x-6 gap-y-3">{(kind === 'kb' ? Object.keys(actionNames).filter(x => x !== 'use') : ['use']).map(action => <label key={action} className="flex items-center gap-2 text-sm"><span>{actionNames[action]}</span><Toggle label={actionNames[action]} checked={change.actions.includes(action)} onChange={on => {
        let actions = on ? [...change.actions, action] : change.actions.filter(x => x !== action)
        if (on && action === 'manage') actions.push('edit', 'query')
        if (on && action === 'edit') actions.push('query')
        if (!on && action === 'query') actions = actions.filter(x => !['edit', 'manage'].includes(x))
        if (!on && action === 'edit') actions = actions.filter(x => x !== 'manage')
        update({ actions: [...new Set(actions)] })
      }} /></label>)}</div>}
      <div className="flex flex-wrap gap-2">
        <Button disabled={!change.subject_id || busy || !grants.data || grants.isError} onClick={save}>{busy ? '处理中…' : '保存授权'}</Button>
        <Button variant="outline" disabled={!change.subject_id || busy} onClick={() => prepare()}>预览变更</Button>
      </div>
    </fieldset>
    {preview && <PreviewPanel value={preview} onSave={save} onCancel={() => setPreview(null)} busy={busy} />}
    <div className="divide-y rounded border">{grants.data?.items.length === 0 && <p className="p-3 text-sm text-muted-foreground">暂无额外授权。</p>}{grants.data?.items.map(g => <div className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm" key={g.subject_type+g.subject_id}><div><p>{g.subject_type === 'role' ? '角色' : '用户'} · {g.name}</p><p className="text-xs text-muted-foreground">{g.effect === 'deny' ? '明确禁止' : g.actions.map(x => actionNames[x]).join('、')}</p></div><div className="flex gap-2"><Button size="sm" variant="ghost" disabled={busy} onClick={() => { setChange(g); setPreview(null) }}>编辑</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => prepare({ ...g, effect: 'remove', actions: [] })}>移除</Button></div></div>)}</div>
    <div className="space-y-2"><label className="text-sm">查看用户最终权限<OptionSelect aria-label="查看最终权限的用户" className={`${accountInput} mt-2`} value={inspect} onValueChange={setInspect} options={[{ value: '', label: '选择用户' }, ...users.data?.map(u => ({ value: u.id, label: u.username })) || []]} /></label>{effective.data && <div className="rounded bg-muted p-3 text-sm"><p>最终权限：{effective.data.actions.map(x => actionNames[x]).join('、') || '无'}</p>{effective.data.reason && <p>{effective.data.reason}</p>}{effective.data.sources.map((s, i) => <p key={i}>{s.source}：{s.effect === 'deny' ? '明确禁止' : s.actions.map(x => actionNames[x]).join('、')}</p>)}</div>}{effective.error && <p className="text-destructive">{effective.error.message}</p>}</div>
  </div>
}
