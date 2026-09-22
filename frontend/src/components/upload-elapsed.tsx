import { useEffect, useState } from 'react'
import type { UploadTask } from '@/lib/api'
import { formatDuration } from '@/lib/format-duration'

export function UploadElapsed({ task }: { task: UploadTask }) {
  const [now, setNow] = useState(Date.now)
  const running = task.status === 'running' || task.status === 'uploading' || task.status === 'cancelling' || task.status === 'cleaning'
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  const end = running ? now : (task.finished_at ?? task.updated_at)
  if (!task.created_at || !end) return null
  return <div className="text-[11px] tabular-nums text-muted-foreground">
    任务历时 {formatDuration(Math.max(0, end - task.created_at))}
    {running && task.updated_at ? ` · 当前步骤 ${formatDuration(Math.max(0, now - task.updated_at))}` : ''}
    {running && <span className="ml-1">（计时不代表处理进度）</span>}
  </div>
}
