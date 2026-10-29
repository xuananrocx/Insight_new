import { useState } from 'react'
import { Download, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { ManagementDialog } from '@/components/management-dialog'
import { api } from '@/lib/api'

type ExportPlan = { total: number; available: number; excluded: { file_id: number; path: string; reason: string }[]; token: string }

export function KbExportButton({ id, name, allowed, compact = false }: { id: string; name: string; allowed: boolean; compact?: boolean }) {
  const [busy, setBusy] = useState(false)
  const [plan, setPlan] = useState<ExportPlan | null>(null)
  async function download(value: ExportPlan, partial: boolean) {
    setBusy(true)
    try {
      await api.kb.downloadPack(id, partial ? `${name}-部分导出` : name, `?allow_partial=${partial}&expected_token=${encodeURIComponent(value.token)}`)
      setPlan(null); toast.success('导出完成，已开始下载')
    } catch (e) { setPlan(null); toast.error((e as Error).message) }
    finally { setBusy(false) }
  }
  async function preview() {
    setBusy(true)
    try {
      const value = await api.kb.exportPreview(id)
      if (value.excluded.length) setPlan(value)
      else await download(value, false)
    } catch (e) { toast.error((e as Error).message) }
    finally { setBusy(false) }
  }
  return <>
    <span title={allowed ? '导出 Pack' : '没有此知识库的导出权限'} className="inline-flex" tabIndex={allowed ? undefined : 0}>
      <Button variant={compact ? 'ghost' : 'outline'} size="sm" disabled={!allowed || busy} aria-label={allowed ? '导出知识库' : '导出知识库：没有权限'} onClick={() => void preview()}>
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}{!compact && <span className="ml-1.5">{busy ? '正在检查与导出' : '导出'}</span>}
      </Button>
    </span>
    {plan && <ManagementDialog title="导出前检查" busy={busy} onClose={() => setPlan(null)} className="max-w-xl">
      <p className="text-sm">共 {plan.total} 个文档，{plan.available} 个可导出，{plan.excluded.length} 个需要处理。</p>
      <div className="max-h-[35dvh] space-y-2 overflow-y-auto rounded-md border p-3">{plan.excluded.map(file => <div key={file.file_id} className="text-xs"><p className="break-all font-medium">{file.path}</p><p className="text-muted-foreground">{file.reason}</p></div>)}</div>
      <p className="text-xs leading-relaxed text-muted-foreground">完整导出需要先修复以上文件。也可仅导出可用文档：包内附遗漏清单，跳过对应向量，不携带全库概览、核心概念和关联，保留可用文档的摘要。不会删除知识库中的任何数据。</p>
      <div className="flex flex-wrap justify-end gap-2"><Button variant="outline" disabled={busy} onClick={() => setPlan(null)}>取消，先修复文件</Button><Button disabled={busy || !plan.available} onClick={() => void download(plan, true)}>{busy ? '正在导出…' : `导出可用文档（${plan.available}）`}</Button></div>
    </ManagementDialog>}
  </>
}
