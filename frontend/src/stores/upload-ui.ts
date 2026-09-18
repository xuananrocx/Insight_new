/**
 * 上传相关 UI 协调状态（跨组件）：
 * - foregroundTaskId：弹窗正在展示的任务（悬浮条据此避免重复完成提示）
 * - viewTask：从悬浮条/banner 点「查看」要打开的任务
 */
import { create } from 'zustand'

type UploadUiState = {
  foregroundTaskId: string | null
  viewTask: { taskId: string; kbId: string } | null
  setForegroundTaskId: (id: string | null) => void
  openView: (taskId: string, kbId: string) => void
  clearView: () => void
}

export const useUploadUiStore = create<UploadUiState>((set) => ({
  foregroundTaskId: null,
  viewTask: null,
  setForegroundTaskId: (id) => set({ foregroundTaskId: id }),
  openView: (taskId, kbId) => set({ viewTask: { taskId, kbId } }),
  clearView: () => set({ viewTask: null }),
}))
