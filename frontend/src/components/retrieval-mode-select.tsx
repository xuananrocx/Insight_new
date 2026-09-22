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
  { key: 'basic', label: '基础检索', desc: '扩大召回、精确词匹配与去重，不调用 AI' },
  { key: 'deep', label: '深度检索', desc: '重排相关资料、补全段落与分散来源，不调用 AI' },
  { key: 'ai', label: 'AI 增强', desc: '检索后由 AI 基于知识库生成流式回答' },
  { key: 'deep_ai', label: '深度AI', desc: 'AI 主动检索和阅读知识库，结合明确标注的通用知识与推断作答，需要 API 支持工具调用' },
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
