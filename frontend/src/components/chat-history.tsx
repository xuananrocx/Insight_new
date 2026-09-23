import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import * as Menu from '@radix-ui/react-dropdown-menu'
import { Plus, Search, Download, Upload, ChevronRight, MoreHorizontal, MessageSquare } from 'lucide-react'
import { toast } from 'sonner'
import { useChatSessionsCtx } from '@/hooks/chat-session-context'
import { api, type SessionSummary } from '@/lib/api'
import { useLocalStorage } from '@/hooks/use-local-storage'
import { useAnswerStreams, abortAnswerStream } from '@/stores/answer-streams'
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '@/components/ui/select'

function Actions({ items }: { items: { label: string; run: () => void }[] }) {
  return <Menu.Root modal={false}><Menu.Trigger asChild><button type="button" aria-label="更多操作" className="rounded p-1 text-muted-foreground hover:bg-accent"><MoreHorizontal className="h-3.5 w-3.5" /></button></Menu.Trigger>
    <Menu.Portal><Menu.Content sideOffset={4} className="z-50 min-w-32 rounded-md border bg-popover p-1 text-xs shadow-lg">
      {items.map(item => <Menu.Item key={item.label} onSelect={item.run} className="cursor-pointer rounded px-3 py-2 outline-none focus:bg-accent">{item.label}</Menu.Item>)}
    </Menu.Content></Menu.Portal>
  </Menu.Root>
}

// 浏览器侧把 Blob 保存成文件
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function ChatHistory() {
  const ctx = useChatSessionsCtx()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const streaming = useAnswerStreams(s => s.streaming)
  const [query, setQuery] = useState('')
  const [exportingAll, setExportingAll] = useState(false)
  const [importing, setImporting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [collapsed, setCollapsed] = useLocalStorage<Record<string, boolean>>('session-group-collapse', {})
  const [action, setAction] = useState<{ kind: string; id?: string; label?: string } | null>(null)
  const [name, setName] = useState('')
  const [target, setTarget] = useState('ungrouped')
  const [busy, setBusy] = useState(false)
  const groups = [...ctx.groups, { id: 'ungrouped', name: '未分组', position: -1 }]
  async function refresh() {
    await Promise.all([qc.invalidateQueries({ queryKey: ['sessions'] }), qc.invalidateQueries({ queryKey: ['session-groups'] }), qc.invalidateQueries({ queryKey: ['session'] })])
  }
  function open(kind: string, id?: string, label = '') { setName(label); setAction({ kind, id, label }) }
  function newSession(groupId: string | null) { ctx.setNewGroupId(groupId); ctx.clearActive(); navigate('/') }
  async function reorder(id: string, direction: 'up' | 'down') {
    try { await api.sessionGroups.update(id, { direction }); await refresh() } catch (error) { toast.error(String(error)) }
  }
  async function save() {
    if (!action || busy) return
    setBusy(true)
    try {
      if (action.kind === '新建分组') await api.sessionGroups.create(name.trim())
      if (action.kind === '重命名分组') await api.sessionGroups.update(action.id!, { name: name.trim() })
      if (action.kind === '删除分组') {
        await api.sessionGroups.remove(action.id!)
        if (ctx.newGroupId === action.id) ctx.setNewGroupId(null)
      }
      if (action.kind === '重命名会话') await ctx.renameSession(action.id!, name.trim())
      if (action.kind === '移动到分组') await api.sessions.update(action.id!, { group_id: target === 'ungrouped' ? null : target })
      if (action.kind === '删除会话') { abortAnswerStream(action.id!); await api.sessions.remove(action.id!); if (ctx.activeId === action.id) ctx.clearActive() }
      await refresh(); setAction(null)
    } catch (error) { toast.error(String(error)) } finally { setBusy(false) }
  }
  function item(s: SessionSummary) {
    return <div key={s.id} className={`flex min-w-0 items-center gap-1 rounded px-2 py-1 ${ctx.activeId === s.id ? 'bg-accent' : 'hover:bg-accent/40'}`}>
      <button className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => { ctx.selectSession(s.id); navigate('/') }}>
        <MessageSquare className="h-3 w-3 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1"><span className="block truncate text-xs" title={s.title}>{s.title}</span>
          <span className="block text-[10px] text-muted-foreground">{streaming[s.id] ? '生成中' : new Date(s.updated_at).toLocaleDateString()} · {s.turn_count}轮</span></span>
      </button>
      <Actions items={[
        { label: '重命名', run: () => open('重命名会话', s.id, s.title) },
        { label: '移动到分组', run: () => { setTarget(s.group_id ?? 'ungrouped'); open('移动到分组', s.id, s.title) } },
        { label: '导出', run: () => { void api.sessions.exportOne(s.id).then(({ blob, filename }) => saveBlob(blob, filename)).catch(error => toast.error(String(error))) } },
        { label: '删除', run: () => open('删除会话', s.id, s.title) },
      ]} />
    </div>
  }
  async function handleExportAll() {
    if (ctx.sessions.length === 0) {
      toast.info('暂无会话可导出')
      return
    }
    setExportingAll(true)
    try {
      const ids = ctx.sessions.map((s) => s.id)
      const { blob, filename } = await api.sessions.exportBatch(ids)
      saveBlob(blob, filename)
      toast.success(`已导出 ${ids.length} 个会话`, { description: filename })
    } catch (e) {
      toast.error('导出失败', { description: (e as Error).message })
    } finally {
      setExportingAll(false)
    }
  }

  async function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''  // 清掉，让用户能再次选同一文件
    if (!file) return
    setImporting(true)
    try {
      const result = await api.sessions.importFile(file)
      await refresh()
      if (result.imported > 0) {
        const lines = result.sessions.map((s) => `• ${s.title}（${s.turn_count} 条）`).join('\n')
        toast.success(`已导入 ${result.imported} 个会话`, { description: lines })
      } else if (result.errors.length > 0) {
        toast.error('导入失败', { description: result.errors[0] })
      } else {
        toast.info('未导入任何会话')
      }
    } catch (e) {
      toast.error('导入失败', { description: (e as Error).message })
    } finally {
      setImporting(false)
    }
  }

  return <div className="flex h-full min-h-0 flex-col">
    <div className="flex shrink-0 items-center justify-between px-2 py-1">
      <span className="text-[10px] font-semibold text-muted-foreground">会话列表</span>
      <div className="flex items-center gap-1">
        <button title="导入会话" disabled={importing} onClick={() => fileInputRef.current?.click()}><Upload className="h-3.5 w-3.5" /></button>
        <button title="导出全部会话" disabled={exportingAll || !ctx.sessions.length} onClick={() => void handleExportAll()}><Download className="h-3.5 w-3.5" /></button>
        <Actions items={[{ label: '新建会话', run: () => newSession(null) }, { label: '新建分组', run: () => open('新建分组') }]} />
      </div>
    </div>
    <input ref={fileInputRef} type="file" accept=".json,.zip" onChange={handleImportFile} className="hidden" />
    <div className="mx-2 my-1 flex items-center gap-1 rounded bg-muted/40 px-2 py-1"><Search className="h-3 w-3" /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索会话或分组…" className="min-w-0 flex-1 bg-transparent text-xs outline-none" /></div>
    <div aria-label="会话列表" className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-1.5">
      {groups.map(group => {
        const matchesGroup = group.name.toLowerCase().includes(query.trim().toLowerCase())
        const sessions = ctx.sessions.filter(s => (s.group_id ?? 'ungrouped') === group.id && (matchesGroup || s.title.toLowerCase().includes(query.trim().toLowerCase())))
        if (query.trim() && !sessions.length && !matchesGroup) return null
        const closed = !query.trim() && collapsed[group.id]
        return <div key={group.id} className="mb-2">
          <div className="flex items-center gap-1">
            <button className="flex min-w-0 flex-1 items-center gap-1 py-1 text-left text-[11px] text-muted-foreground" onClick={() => setCollapsed({ ...collapsed, [group.id]: !collapsed[group.id] })} aria-expanded={!closed}>
              <ChevronRight className={`h-3 w-3 shrink-0 ${closed ? '' : 'rotate-90'}`} /><span className="truncate">{group.name}</span><span>{sessions.length}</span>
            </button>
            <button title={`在${group.name}中新建会话`} onClick={() => newSession(group.id === 'ungrouped' ? null : group.id)}><Plus className="h-3 w-3" /></button>
            {group.id !== 'ungrouped' && <Actions items={[
              { label: '重命名分组', run: () => open('重命名分组', group.id, group.name) },
              { label: '上移', run: () => void reorder(group.id, 'up') },
              { label: '下移', run: () => void reorder(group.id, 'down') },
              { label: '删除分组', run: () => open('删除分组', group.id, group.name) },
            ]} />}
          </div>
          {!closed && (sessions.length ? sessions.map(item) : <p className="px-5 py-1 text-[10px] text-muted-foreground">暂无会话</p>)}
        </div>
      })}
    </div>
    <Dialog open={!!action} onOpenChange={value => { if (!value) setAction(null) }}><DialogContent>
      <DialogTitle>{action?.kind}</DialogTitle><DialogDescription className="mt-2">{action?.kind === '删除分组' ? '组内会话将移到未分组，不会删除。' : action?.kind === '删除会话' ? `删除「${action.label}」及其问答记录？` : action?.kind === '移动到分组' ? '仅调整归类，不改变会话的知识库或上下文。' : '名称最多80字。'}</DialogDescription>
      <form className="mt-4 space-y-4" onSubmit={e => { e.preventDefault(); void save() }}>
        {action?.kind === '移动到分组' ? <Select value={target} onValueChange={setTarget}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{groups.map(g => <SelectItem key={g.id} value={g.id}>{g.name}</SelectItem>)}</SelectContent></Select> : !action?.kind.startsWith('删除') && <input autoFocus aria-label="名称" maxLength={80} value={name} onChange={e => setName(e.target.value)} className="w-full rounded border bg-background px-3 py-2 text-sm" />}
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={() => setAction(null)}>取消</Button><Button type="submit" disabled={busy || (!action?.kind.startsWith('删除') && action?.kind !== '移动到分组' && !name.trim())}>{action?.kind.startsWith('删除') ? '删除' : '保存'}</Button></div>
      </form>
    </DialogContent></Dialog>
  </div>
}
