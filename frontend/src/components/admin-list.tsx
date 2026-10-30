import { useState, type ReactNode } from 'react'
import * as Menu from '@radix-ui/react-dropdown-menu'
import { ChevronLeft, ChevronRight, MoreHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function StatusLabel({ enabled }: { enabled: boolean }) {
  return <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs"><span className={`h-1.5 w-1.5 rounded-full ${enabled ? 'bg-emerald-600 dark:bg-emerald-400' : 'bg-muted-foreground/60'}`} />{enabled ? '启用' : '停用'}</span>
}
export function ListTag({ children }: { children: ReactNode }) {
  return <span className="inline-block rounded border bg-muted/30 px-1.5 py-0.5 text-[11px] font-normal">{children}</span>
}
export function ListMore({ label, items }: { label: string; items: { label: string; action: () => void; destructive?: boolean; disabled?: boolean }[] }) {
  if (!items.length) return null
  return <Menu.Root><Menu.Trigger asChild><Button variant="ghost" size="sm" aria-label={label} className="h-8 w-8 p-0"><MoreHorizontal className="h-4 w-4" /></Button></Menu.Trigger><Menu.Portal><Menu.Content align="end" sideOffset={6} className="z-50 min-w-36 rounded-lg border bg-popover p-1 text-popover-foreground shadow-md">{items.map(item => <Menu.Item key={item.label} disabled={item.disabled} onSelect={item.action} className={`cursor-pointer rounded px-3 py-2 text-sm outline-none focus:bg-accent data-[disabled]:pointer-events-none data-[disabled]:opacity-50 ${item.destructive ? 'text-destructive' : ''}`}>{item.label}</Menu.Item>)}</Menu.Content></Menu.Portal></Menu.Root>
}
export type ListColumn<T> = { label: string; render: (row: T) => ReactNode; secondary?: boolean; className?: string }
export function AdminList<T>({ rows, columns, rowKey, loading, error, empty = '没有匹配记录', filterKey = '', label, prefix = '' }: {
  rows: T[]; columns: ListColumn<T>[]; rowKey: (row: T) => string | number; loading?: boolean; error?: Error | null; empty?: string; filterKey?: string; label: string; prefix?: string
}) {
  const [pagination, setPagination] = useState({ key: filterKey, page: 1 })
  const pages = Math.max(1, Math.ceil(rows.length / 20))
  const page = pagination.key === filterKey ? Math.min(pagination.page, pages) : 1
  if (pagination.key !== filterKey || pagination.page !== page) {
    setPagination({ key: filterKey, page })
  }
  const go = (next: number) => setPagination({ key: filterKey, page: next })
  return <div className="space-y-3">
    <div className="overflow-hidden rounded-lg border bg-card text-card-foreground">
      <table aria-label={label} className="w-full table-fixed text-left text-[13px]">
        <thead className="bg-muted/35 text-xs text-muted-foreground"><tr>{columns.map((c, i) => <th key={i} scope="col" className={`px-3 py-3 font-normal md:px-4 ${c.secondary ? 'hidden md:table-cell' : ''} ${c.className || ''}`}>{c.label}</th>)}</tr></thead>
        <tbody>{loading || error || !rows.length ? <tr><td colSpan={columns.length} className="px-4 py-8 text-center text-sm text-muted-foreground"><span role={error ? 'alert' : 'status'}>{loading ? '加载中…' : error ? error.message : empty}</span></td></tr> : rows.slice((page - 1) * 20, page * 20).map(row => <tr key={rowKey(row)} className="border-t transition-colors hover:bg-muted/30">{columns.map((c, i) => <td key={i} className={`break-words px-3 py-3 align-middle md:px-4 [overflow-wrap:anywhere] ${c.secondary ? 'hidden md:table-cell' : ''} ${c.className || ''}`}>{c.render(row)}</td>)}</tr>)}</tbody>
      </table>
    </div>
    {!loading && !error && <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span aria-live="polite">{prefix}共 {rows.length} 条 · 每页 20 条</span><div className="flex items-center gap-2"><Button size="sm" variant="ghost" aria-label="上一页" disabled={page === 1} onClick={() => go(page - 1)}><ChevronLeft className="h-4 w-4" /></Button><span>{page} / {pages}</span><Button size="sm" variant="ghost" aria-label="下一页" disabled={page === pages} onClick={() => go(page + 1)}><ChevronRight className="h-4 w-4" /></Button></div></div>}
  </div>
}
