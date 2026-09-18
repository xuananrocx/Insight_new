import { Search } from 'lucide-react'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { RetrievalMode } from '@/lib/api'

export const RETRIEVAL_MODES: { key: RetrievalMode; label: string; desc: string }[] = [
  { key: 'basic', label: '基础检索', desc: '向量 + 关键词混合检索，不调用 AI，速度最快' },
  { key: 'deep', label: '深度检索', desc: '扩展查询 + 合并相邻段落 + 关键词高亮，不调用 AI' },
  { key: 'ai', label: 'AI 增强', desc: '检索后由 AI 基于知识库生成流式回答' },
]

type Props = {
  value: RetrievalMode
  onChange: (v: RetrievalMode) => void
  className?: string
}

export function RetrievalModeSelect({ value, onChange, className }: Props) {
  const active = RETRIEVAL_MODES.find((m) => m.key === value) ?? RETRIEVAL_MODES[2]
  return (
    <div
      className={
        'inline-flex items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-[11px] ' +
        (className ?? '')
      }
      title={`检索模式：${active.desc}`}
    >
      <Search className="h-3.5 w-3.5 text-muted-foreground" />
      <span className="text-muted-foreground">检索模式</span>
      <Select
        value={active.key}
        onValueChange={(k) => {
          const m = RETRIEVAL_MODES.find((m) => m.key === k)
          if (m) onChange(m.key)
        }}
      >
        <SelectTrigger className="h-auto w-auto gap-0.5 border-0 bg-transparent px-1 py-0 text-[11px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent className="min-w-0">
          {RETRIEVAL_MODES.map((m) => (
            <SelectItem key={m.key} value={m.key} className="pr-6" title={m.desc}>
              {m.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
