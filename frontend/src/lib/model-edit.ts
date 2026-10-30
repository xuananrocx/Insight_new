export type ModelEditImpact = {
  renames: { old_model_id: string; model_id: string; defaults: number; sessions: number }[]
  deletions: { model_id: string; defaults: number; sessions: number }[]
  confirmation_token: string
}

export function modelEditMessage(impact: ModelEditImpact, team: boolean): string | null {
  if (!impact.renames.length && !impact.deletions.length) return null
  const lines = [team ? '本次修改影响此团队 API 的所有关联用户：' : '本次修改将影响以下模型选择：']
  for (const model of impact.renames) {
    lines.push(`修改 ${model.old_model_id} → ${model.model_id}：同步 ${model.defaults} 个默认选择、${model.sessions} 个会话选择。`)
  }
  for (const model of impact.deletions) {
    lines.push(`删除 ${model.model_id}：${model.defaults} 个默认选择、${model.sessions} 个会话选择将失效，需要重新选择。`)
  }
  lines.push('历史回答和任务记录不变。使用原模型的运行中请求可能中断；任务保留已完成结果，恢复时需确认模型。继续保存？')
  return lines.join('\n')
}
