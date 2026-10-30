import { useEffect, useState } from 'react'
import { CheckCircle2, CircleAlert, Loader2 } from 'lucide-react'
import { ManagementDialog } from '@/components/management-dialog'
import { Button } from '@/components/ui/button'

export type WebSearchTest = {
  providerId: string
  modelId: string
  label: string
  startedAt: number
  elapsedMs?: number
  status: 'pending' | 'success' | 'unconfirmed' | 'error' | 'cancelled'
  message: string
  sources: { title: string; url: string }[]
}

export function WebSearchTestDialog({ test, onClose, onStop }: { test: WebSearchTest; onClose: () => void; onStop: () => void }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (test.status !== 'pending') return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [test.status])
  const pending = test.status === 'pending'
  const success = test.status === 'success'
  const seconds = Math.max(0, Math.floor((test.elapsedMs ?? now - test.startedAt) / 1000))
  const title = pending ? '正在测试联网搜索' : success ? '测试成功' : test.status === 'cancelled' ? '已中止测试' : test.status === 'unconfirmed' ? '测试未通过' : '测试未完成'
  const Icon = pending ? Loader2 : success ? CheckCircle2 : CircleAlert
  return <ManagementDialog title="联网搜索测试" onClose={onClose} className="max-w-xl h-[min(440px,90dvh)]">
    <p className="text-sm text-muted-foreground">{test.label}</p>
    <div role="status" aria-live="polite" className="rounded-lg border bg-muted/30 p-4">
      <div className="flex items-center gap-2">
        <Icon aria-hidden="true" className={`h-5 w-5 shrink-0 ${pending ? 'animate-spin text-primary motion-reduce:animate-none' : success ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}`} />
        <span className="font-medium">{title}</span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{test.message}</p>
    </div>
    <p className="text-xs tabular-nums text-muted-foreground">{pending ? '已等待' : '耗时'} {seconds} 秒{pending ? ' · 服务端测试最多等待 90 秒' : ''}</p>
    {pending && <p className="text-xs leading-relaxed text-muted-foreground">正在验证实际搜索调用和网页来源，不只是检查接口连通性。关闭弹窗不会停止测试，可点击模型旁“测试中…”重新查看。</p>}
    {pending && <div className="flex gap-2"><Button variant="outline" onClick={onClose}>收起，继续测试</Button><Button variant="outline" onClick={onStop}>中止测试</Button></div>}
    {test.status === 'unconfirmed' && <p className="text-xs text-muted-foreground">本次未取得完整的搜索证据，不代表模型一定不支持联网；请检查配置或稍后手动重试。</p>}
    {test.sources.length > 0 && <div><h3 className="mb-2 text-sm font-medium">{success ? '已返回网页来源' : '本次返回的网页来源'} · {test.sources.length}</h3><ul className="space-y-2 text-sm">{test.sources.map(source => <li key={source.url}><a href={source.url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">{source.title}</a></li>)}</ul></div>}
  </ManagementDialog>
}
