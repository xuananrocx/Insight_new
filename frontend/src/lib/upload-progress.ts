import type { UploadTask } from './api'

const stages: Record<string, string> = {
  parsing: '解析并切片', chunking: '切片', embedding: '向量化', upserting: '写入索引',
  publishing: '提交新索引', ai_summary: '生成 AI 摘要', processing: '准备处理', resuming: '等待恢复', retrying: '等待重试',
}

export function uploadProgressView(task: UploadTask) {
  const finished = task.done + task.skipped + task.failed
  if (task.status === 'completed') return {
    label: task.failed ? '处理结束，存在失败' : task.skipped === task.total ? '已跳过' : '导入完成',
    percent: task.failed ? null : 100,
  }
  if (task.status === 'cancelling') return {label: '正在取消，等待当前操作结束', percent: null}
  if (task.status === 'cleaning') return {label: '正在清理本次数据', percent: null}
  if (task.status === 'cleanup_failed') return {label: '清理未完成，请重试', percent: null}
  if (task.status === 'cancelled') return {label: '已取消', percent: null}
  if (task.status === 'paused' || task.status === 'failed') return {label: '已中断，等待恢复', percent: null}
  if (task.status === 'uploading') return {label: '上传中', percent: null}
  return task.total === 1
    ? {label: stages[task.current_stage ?? ''] ?? '等待处理', percent: task.current_percent ?? null}
    : {label: '导入中', percent: task.total ? Math.round(finished / task.total * 100) : null}
}
