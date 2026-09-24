import { Settings2 } from 'lucide-react'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const TIERS = [
  { key: 'low', label: '低', topK: 5 },
  { key: 'medium', label: '中', topK: 10 },
  { key: 'high', label: '高', topK: 15 },
  { key: 'max', label: '最大', topK: 20 },
] as const

type Props = {
  value: number
  onChange: (v: number) => void
  className?: string
}

export function TopKSelect({ value, onChange, className }: Props) {
  const active = TIERS.find((t) => t.topK === value) ?? TIERS[1]
  return (
    <div
      className={
        'inline-flex items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-[11px] ' +
        (className ?? '')
      }
      title={`当前 ${active.topK} 条：基础检索控制结果数量；增强 AI 控制参考资料上限；深度 AI 控制单次搜索默认数量，AI 显式指定数量时优先。不控制查阅轮数或整轮资料总量。`}
    >
      <Settings2 className="h-3.5 w-3.5 text-muted-foreground" />
      <span className="text-muted-foreground">答案深度</span>
      <Select
        value={active.key}
        onValueChange={(k) => {
          const tier = TIERS.find((t) => t.key === k)
          if (tier) onChange(tier.topK)
        }}
      >
        <SelectTrigger className="h-auto w-auto gap-0.5 border-0 bg-transparent px-1 py-0 text-[11px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent className="min-w-0">
          {TIERS.map((t) => (
            <SelectItem key={t.key} value={t.key} className="pl-3 pr-6">
              {t.label} · {t.topK} 段
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
