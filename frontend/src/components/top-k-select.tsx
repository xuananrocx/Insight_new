import { useState, type ReactElement } from 'react'
import { Info, Settings2 } from 'lucide-react'
import * as Tooltip from '@radix-ui/react-tooltip'
import type { RetrievalMode } from '@/lib/api'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

const OPTIONS = {
  basic: { label: '检索深度', description: '最多展示多少条匹配资料。实际数量可能因匹配不足或去重而减少。', hint: '实际结果可能少于所选数量' },
  ai: { label: '检索深度', description: '控制提供给 AI 的资料窗口数量上限。更多资料不一定回答更好，实际内容还受去重及上下文预算限制。', hint: '资料更多不代表回答更好' },
  deep_ai: { label: '检索深度', description: '控制 AI 未指定数量时，单次搜索默认返回多少条。AI 明确指定数量时优先使用其选择；不控制查阅轮数或整轮资料总量。', hint: 'AI 可自行指定数量' },
} as const

const TIERS = [{ value: 5, label: '低' }, { value: 10, label: '中' }, { value: 15, label: '高' }, { value: 20, label: '最高' }]

function DepthHint({ text, children }: { text: string; children: ReactElement }) {
  return <Tooltip.Provider delayDuration={200}><Tooltip.Root>
    <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
    <Tooltip.Portal><Tooltip.Content side="right" sideOffset={10} collisionPadding={12} className="z-[90] max-w-[min(300px,calc(100vw-24px))] rounded-md border bg-popover p-3 text-xs leading-relaxed text-popover-foreground shadow-md">{text}</Tooltip.Content></Tooltip.Portal>
  </Tooltip.Root></Tooltip.Provider>
}

export function TopKSelect({ value, onChange, mode, className = '' }: {
  value: number; onChange: (value: number) => void; mode: RetrievalMode; className?: string
}) {
  const info = OPTIONS[mode === 'deep' ? 'basic' : mode]
  const [open, setOpen] = useState(false)
  const quantity = (n: number) => mode === 'deep_ai'
    ? `默认每次搜索最多返回 ${n} 条；AI 明确指定数量时优先使用其选择。`
    : mode === 'ai' ? `最多选取 ${n} 个资料窗口，实际内容受去重及上下文预算限制。`
    : `最多展示 ${n} 条匹配资料，实际数量可能因匹配不足或去重而减少。`
  const activeLabel = TIERS.find(tier => tier.value === value)?.label ?? '中'
  return <div className={`inline-flex items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-[11px] ${className}`}>
    <Settings2 className="h-3.5 w-3.5 text-muted-foreground" />
    <Tooltip.Provider delayDuration={200}><Tooltip.Root open={open} onOpenChange={setOpen}>
      <Tooltip.Trigger asChild>
        <button type="button" aria-label={`${info.label}说明`} className="inline-flex items-center gap-1 rounded text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onPointerDown={event => event.preventDefault()} onClick={() => setOpen(value => !value)}>
          <span>{info.label}</span><Info className="h-3 w-3" />
        </button>
      </Tooltip.Trigger>
      <Tooltip.Portal><Tooltip.Content side="right" sideOffset={10} collisionPadding={12} className="z-[90] max-w-[min(300px,calc(100vw-24px))] rounded-md border bg-popover p-3 text-xs leading-relaxed text-popover-foreground shadow-md">{activeLabel}：{quantity(value)} {info.description}</Tooltip.Content></Tooltip.Portal>
    </Tooltip.Root></Tooltip.Provider>
    <Select value={String(value)} onValueChange={v => onChange(Number(v))}>
      <DepthHint text={`${activeLabel}：${quantity(value)}`}><SelectTrigger aria-label={info.label} className="h-auto w-auto gap-0.5 border-0 bg-transparent px-1 py-0 text-[11px]"><SelectValue /></SelectTrigger></DepthHint>
      <SelectContent>
        {TIERS.map(tier => <DepthHint key={tier.value} text={`${tier.label}：${quantity(tier.value)}`}><SelectItem value={String(tier.value)} className="pl-3 pr-6">{tier.label}</SelectItem></DepthHint>)}
        <p className="border-t px-3 py-2 text-[11px] text-muted-foreground">{info.hint}</p>
      </SelectContent>
    </Select>
  </div>
}
