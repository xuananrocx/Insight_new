import type { QaTraceStage } from '@/lib/api'

// SSE 阶段事件的本地合并规则：
// - status="running" 是阶段开始的瞬时标记，直接追加展示
// - 完成事件到达时移除最后一条 running 行再追加，避免残留
export function mergeStage(stages: QaTraceStage[], incoming: QaTraceStage): QaTraceStage[] {
  if (incoming.status === 'running') return [...stages, incoming]
  const next = [...stages]
  for (let i = next.length - 1; i >= 0; i--) {
    if (next[i].status === 'running') {
      next.splice(i, 1)
      break
    }
  }
  next.push(incoming)
  return next
}

export function stripRunningStages(stages: QaTraceStage[]): QaTraceStage[] {
  return stages.filter((s) => s.status !== 'running')
}
