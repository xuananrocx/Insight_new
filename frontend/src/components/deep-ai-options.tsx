import { OptionSelect } from '@/components/ui/select'
import { type AiConstraintStrategy, useDeepAiOptions } from '@/hooks/use-deep-ai-options'
import { Toggle } from '@/components/ui/toggle-switch'

export function DeepAiOptionsFields() {
  const [value, setValue] = useDeepAiOptions()
  return <div className="space-y-3 border-t pt-3 text-sm">
    <div className="space-y-2"><label className="flex items-center justify-between gap-4"><span>AI约束策略</span><OptionSelect aria-label="AI约束策略" value={value.constraint_strategy} onValueChange={v => setValue({ ...value, constraint_strategy: v as AiConstraintStrategy })} options={[{ value: 'evidence', label: '资料优先' }, { value: 'balanced', label: '综合分析（默认）' }, { value: 'exploratory', label: '开放探索' }]} /></label><p className="text-xs text-muted-foreground">{value.constraint_strategy === 'evidence' ? '依据知识库原文及其支持的推导回答，不补充外部通用知识。' : value.constraint_strategy === 'exploratory' ? '结合知识库探索更多假设和替代方案，说明关键假设及验证方法。' : '以知识库为基础，结合通用知识分析原因，给出判断与验证方法。'} 所有策略均使用真实引用，不改变查阅轮数和权限。</p></div>
    <div className="flex items-center justify-between gap-4"><span>不限制查阅轮数</span><Toggle label="不限制查阅轮数" checked={value.max_rounds === null} onChange={unlimited => setValue({ ...value, max_rounds: unlimited ? null : 10 })} /></div>
    {value.max_rounds !== null && <label className="flex items-center justify-between gap-4"><span>最大查阅轮数</span><input aria-label="最大查阅轮数" type="number" min={1} step={1} value={value.max_rounds} onChange={e => { const n = Number(e.target.value); if (Number.isSafeInteger(n) && n >= 1) setValue({ ...value, max_rounds: n }) }} className="w-24 rounded border bg-background px-3 py-2" /></label>}
    <div className="flex items-center justify-between gap-4"><span>限制单次深度 AI 总时长</span><Toggle label="限制单次深度 AI 总时长" checked={value.time_limit_enabled} onChange={enabled => setValue({ ...value, time_limit_enabled: enabled })} /></div>
    <label className="flex items-center justify-between gap-4"><span>总时长上限（分钟）</span><input aria-label="总时长上限（分钟）" type="number" min={1} step={1} disabled={!value.time_limit_enabled} value={value.total_timeout_seconds / 60} onChange={e => { const n = Number(e.target.value); if (Number.isSafeInteger(n) && n >= 1 && Number.isSafeInteger(n * 60)) setValue({ ...value, total_timeout_seconds: n * 60 }) }} className="w-24 rounded border bg-background px-3 py-2 disabled:opacity-50" /></label>
    <p className="text-xs leading-relaxed text-muted-foreground">自动保存。默认最多查阅 10 轮；总时长预设 6 分钟，默认关闭限制。不限轮数或总时长时，仍保留单次 API 超时、资料容量和重复查阅保护，可随时手动停止。资料充分时会提前回答。</p>
  </div>
}
