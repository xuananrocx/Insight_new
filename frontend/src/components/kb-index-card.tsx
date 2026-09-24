import { useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, RefreshCw, ChevronDown, ChevronRight } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog'

const labels: Record<string, string> = { ready: '已就绪', missing: '待补建', unavailable: '尚未入库', queued: '排队中', running: '建设中', cancelling: '正在取消', cancelled: '已取消', done: '已完成', partial: '部分失败', failed: '失败' }
const active = (state: string) => ['queued', 'running', 'cancelling'].includes(state)

export function KbIndexCard({ kbId, canManage }: { kbId: string; canManage: boolean }) {
  const client = useQueryClient()
  const contentId = useId()
  const [expanded, setExpanded] = useState(false)
  const [selection, setSelection] = useState<{ force: boolean; file_ids?: number[]; label: string } | null>(null)
  const query = useQuery({ queryKey: ['kb-indexes', kbId], queryFn: () => api.kb.indexes.get(kbId), refetchInterval: query => query.state.data?.tasks.some(task => active(task.state)) ? 2000 : 30000 })
  const build = useMutation({
    mutationFn: (body: { force: boolean; file_ids?: number[] }) => api.kb.indexes.rebuild(kbId, body),
    onSuccess: () => { setSelection(null); client.invalidateQueries({ queryKey: ['kb-indexes', kbId] }); toast.success('索引任务已在后台开始') },
    onError: (error: Error) => toast.error(error.message),
  })
  const cancel = useMutation({
    mutationFn: (id: string) => api.kb.indexes.cancel(kbId, id),
    onSuccess: () => client.invalidateQueries({ queryKey: ['kb-indexes', kbId] }),
    onError: (error: Error) => toast.error(error.message),
  })
  const files = query.data?.files ?? []
  const task = query.data?.tasks.find(task => active(task.state)) ?? query.data?.tasks[0]
  const busy = !!task && active(task.state)
  const summary = !query.data
    ? query.isError ? '加载失败' : '正在加载'
    : files.length ? `${files.filter(f => f.status === 'ready').length}/${files.length} 已就绪` : '暂无文档'
  const failedTask = !!task && ['partial', 'failed'].includes(task.state)
  return <Card className="mb-6 overflow-hidden">
    <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
      <button type="button" aria-expanded={expanded} aria-controls={contentId} className="flex flex-wrap items-center gap-2 text-sm font-medium" onClick={() => setExpanded(!expanded)}>
        {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}<Database className="h-4 w-4" />文档索引
        <span className="text-xs text-muted-foreground">{summary}</span>
      </button>
      {busy && <span role="status" className="text-xs text-primary">{labels[task.state]} · {task.completed}/{task.total} 份</span>}
      {failedTask && <span role="status" className="text-xs text-destructive">最近任务：{labels[task.state]}{task.failed > 0 && ` · ${task.failed} 份失败`}</span>}
      {query.isError && query.data && <span role="status" className="text-xs text-destructive">更新失败，显示上次结果</span>}
    </div>
    {expanded && <div id={contentId} className="border-t px-5 py-4">
      <p className="text-xs text-muted-foreground">导入时自动建立。索引不重新计算向量，失败时保留已生效的索引。{canManage ? '已有文档可补建或单独重建。' : '补建或重建请联系知识库管理者。'}</p>
      {query.error && <p role="alert" className="mt-2 text-sm text-destructive">{query.error.message}</p>}
      {canManage && <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={busy || query.isLoading || query.isError || !files.length} onClick={() => setSelection({ force: false, label: '补建缺失或过期索引' })}>补建索引</Button>
        <Button size="sm" variant="outline" disabled={busy || query.isError || !files.length} onClick={() => setSelection({ force: true, label: '重建全部文档索引' })}><RefreshCw className="mr-1 h-3.5 w-3.5" />全部重建</Button>
      </div>}
      {task && <div className="mt-3 rounded border p-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-3"><span>{busy ? '当前任务' : '最近任务'}：{labels[task.state]} · 已处理 {task.completed}/{task.total} 份文档{task.failed > 0 && ` · ${task.failed} 份失败`}</span>
          {canManage && busy && <Button variant="ghost" size="sm" disabled={cancel.isPending || task.state === 'cancelling'} onClick={() => cancel.mutate(task.id)}>安全取消</Button>}</div>
        {task.state !== 'done' && <p className="mt-1 break-words text-xs text-muted-foreground">{task.detail}</p>}
        {busy && <progress aria-label="文档索引建设进度" className="mt-2 h-2 w-full" max={Math.max(1, task.total)} value={task.completed} />}
        {task.results.filter(r => r.status === 'failed').map(r => <p key={r.file_id} className="mt-1 break-words text-xs text-destructive">{r.name}：{r.error}</p>)}
      </div>}
      <div className="mt-3 max-h-80 divide-y overflow-auto">
        {files.map(file => <div key={file.id} className="flex items-center gap-3 py-2 text-sm">
          <span className="min-w-0 flex-1 break-all">{file.name}</span><span className="shrink-0 text-xs text-muted-foreground">{labels[file.status]}</span>
          {canManage && <Button size="sm" variant="ghost" disabled={busy || query.isError || file.status === 'unavailable'} onClick={() => setSelection({ force: true, file_ids: [file.id], label: `重建 ${file.name} 的索引` })}>重建</Button>}
        </div>)}
      </div>
    </div>}
    <Dialog open={!!selection} onOpenChange={open => { if (!open) setSelection(null) }}>
      <DialogContent><DialogTitle>重建文档索引</DialogTitle><DialogDescription className="mt-3 break-words">{selection?.label}。任务在后台执行，可关闭页面后返回查看进度。原文件已变化的文档需要先重新导入。</DialogDescription>
        <div className="mt-4 flex justify-end gap-2"><Button variant="outline" onClick={() => setSelection(null)}>取消</Button><Button disabled={build.isPending} onClick={() => { if (selection) build.mutate({ force: selection.force, file_ids: selection.file_ids }) }}>{build.isPending ? '提交中…' : '开始'}</Button></div>
      </DialogContent>
    </Dialog>
  </Card>
}
