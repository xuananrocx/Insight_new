import { accountApi } from '@/lib/account-api'
import { api } from '@/lib/api'
import { useConfirm } from '@/components/confirmation-provider'

export function useResumeUpload() {
  const confirm = useConfirm()
  return async (taskId: string) => {
    const selection = await accountApi.apiPreferences()
    const message = selection.valid
      ? `恢复任务后，需要 AI 的步骤将使用“${selection.provider_name} · ${selection.model_label}”。继续？`
      : `当前默认模型不可用：${selection.reason}。恢复后只处理文档和向量化，跳过需要 AI 的步骤。继续？`
    if (!await confirm(message)) return false
    await api.knowledge.resumeUploadTask(taskId, {
      provider_id: selection.valid ? selection.provider_id! : '',
      model_id: selection.valid ? selection.model_id : null,
    })
    return true
  }
}
