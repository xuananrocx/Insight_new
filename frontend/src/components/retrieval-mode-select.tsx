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
  { key: 'basic', label: '基础检索', desc: '直接展示知识库片段，可对本轮结果进行扩展检索' },
  { key: 'ai', label: '增强AI', desc: '检索后由 AI 基于知识库生成流式回答' },
  { key: 'deep_ai', label: '深度AI', desc: 'AI 主动检索和阅读知识库，结合明确标注的通用知识与推断作答，需要 API 支持工具调用' },
]

type Props = {
  value: RetrievalMode
  onChange: (v: RetrievalMode) => void
  className?: string
}

export function RetrievalModeSelect({ value, onChange, className }: Props) {
  const active = RETRIEVAL_MODES.find((m) => m.key === (value === 'deep' ? 'basic' : value)) ?? RETRIEVAL_MODES[1]
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
