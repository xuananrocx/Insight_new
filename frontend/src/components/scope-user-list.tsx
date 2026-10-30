import { useState } from 'react'
import { Toggle } from '@/components/ui/toggle-switch'
export function ScopeUserList({ users, selected, onChange }: {
  users: { id: string; username: string; disabled?: boolean }[]
  selected: string[]
  onChange: (id: string, on: boolean) => void
}) {
  const [search, setSearch] = useState('')
  const [selectedOnly, setSelectedOnly] = useState(false)
  const filtered = users.filter(u => u.username.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()) && (!selectedOnly || selected.includes(u.id)))
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span>额外可见用户 · 已选 {selected.length} 人</span><label className="flex items-center gap-1.5"><input type="checkbox" checked={selectedOnly} onChange={e => setSelectedOnly(e.target.checked)} />仅看已选</label></div>
    <input aria-label="搜索额外可见用户" placeholder="搜索用户名" value={search} onChange={e => setSearch(e.target.value)} className="w-full rounded-md border bg-background px-3 py-2 text-sm" />
    <div className="max-h-[min(280px,35dvh)] overflow-y-auto overscroll-contain rounded-md border" role="group" aria-label="额外可见用户">
      {filtered.map(u => <label key={u.id} className="flex items-center justify-between gap-3 border-b px-3 py-2 text-sm last:border-0"><span className="min-w-0 break-all">{u.username}{!!u.disabled && <span className="ml-1 text-xs text-muted-foreground">（停用，不生效）</span>}</span><Toggle label={`可见用户 ${u.username}`} checked={selected.includes(u.id)} onChange={on => onChange(u.id, on)} /></label>)}
      {!filtered.length && <p className="p-4 text-center text-xs text-muted-foreground">没有匹配的用户</p>}
    </div>
  </div>
}
