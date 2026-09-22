import { useAuth } from '@/hooks/use-auth'
import { useQuery } from '@tanstack/react-query'
import {
  Library,
  Layers,
  Inbox,
  CheckCircle2,
  Loader2,
} from 'lucide-react'

import { Card } from '@/components/ui/card'
import { api } from '@/lib/api'

export function StatsCards() {
  const { can } = useAuth()
  const allowed = can('analysis.view') || can('documents.view') || can('feedback.view')
  const { data, isLoading } = useQuery({
    queryKey: ['knowledge', 'stats'],
    queryFn: () => api.knowledge.stats(),
    refetchInterval: 10000,
    enabled: allowed,
  })

  if (!allowed) return null
  const stats = [
    {
      label: '文档总数',
      value: data?.files_total,
      sub: data ? `${data.files_done} 已入库` : '',
      icon: Library,
      tone: 'default' as const,
    },
    {
      label: '知识片段',
      value: data?.total_chunks,
      sub: '向量化的语义单元',
      icon: Layers,
      tone: 'default' as const,
    },
    {
      label: '待审批',
      value: data?.feedback_pending,
      sub: '点赞的问答',
      icon: Inbox,
      tone: data?.feedback_pending ? 'warning' : 'default' as const,
    },
    {
      label: '已沉淀',
      value: data?.feedback_approved,
      sub: '入库的案例',
      icon: CheckCircle2,
      tone: 'default' as const,
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {stats.map((stat) => {
        const Icon = stat.icon
        const toneClass =
          stat.tone === 'warning' ? 'text-warning' : 'text-muted-foreground'
        return (
          <Card key={stat.label} className="p-4">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {stat.label}
              </span>
              <Icon className="h-3.5 w-3.5 text-muted-foreground/70" />
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              ) : (
                <span className={`text-[22px] font-semibold tracking-tight ${toneClass}`}>
                  {stat.value ?? '-'}
                </span>
              )}
            </div>
            <div className="mt-1 text-[10px] text-muted-foreground">{stat.sub}</div>
          </Card>
        )
      })}
    </div>
  )
}
