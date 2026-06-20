import { BarChart3, TrendingUp, MessageSquare, Target } from 'lucide-react'

import { Card } from '@/components/ui/card'
import { StatsCards } from '@/components/stats-cards'

const dailyData = [
  { day: '周一', value: 38 },
  { day: '周二', value: 52 },
  { day: '周三', value: 41 },
  { day: '周四', value: 67 },
  { day: '周五', value: 58 },
  { day: '周六', value: 23 },
  { day: '周日', value: 19 },
]

const topErrors = [
  { code: 'ERR-004', title: '心跳超时', count: 12 },
  { code: 'ERR-005', title: '行情中断', count: 8 },
  { code: 'ERR-002', title: '多播组加入失败', count: 6 },
  { code: 'ERR-007', title: 'Sequence Gap', count: 4 },
]

export function StatsPage() {
  const max = Math.max(...dailyData.map((d) => d.value))

  return (
    <div className="mx-auto max-w-4xl px-8 py-8">
      <div className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight">分析</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          使用情况与系统表现概览
        </p>
      </div>

      <StatsCards />

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BarChart3 className="h-4 w-4 text-muted-foreground" />
              <span className="text-[14px] font-medium">本周提问量</span>
            </div>
            <span className="inline-flex items-center gap-1 text-[11px] text-success">
              <TrendingUp className="h-3 w-3" /> +18%
            </span>
          </div>
          <div className="flex h-32 items-end gap-2">
            {dailyData.map((d) => (
              <div key={d.day} className="flex flex-1 flex-col items-center gap-2">
                <div
                  className="w-full rounded-md bg-gradient-to-t from-primary/40 to-primary transition-all"
                  style={{ height: `${(d.value / max) * 100}%` }}
                />
                <span className="text-[10px] text-muted-foreground">{d.day}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <div className="mb-4 flex items-center gap-2">
            <Target className="h-4 w-4 text-muted-foreground" />
            <span className="text-[14px] font-medium">高频问题 Top 4</span>
          </div>
          <div className="space-y-2">
            {topErrors.map((e, i) => (
              <div key={e.code} className="flex items-center gap-3">
                <span className="text-[11px] text-muted-foreground">{i + 1}.</span>
                <span className="w-16 font-mono text-[12px] text-primary">{e.code}</span>
                <span className="flex-1 text-[13px]">{e.title}</span>
                <span className="text-[11px] text-muted-foreground">{e.count} 次</span>
                <MessageSquare className="h-3 w-3 text-muted-foreground" />
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  )
}
