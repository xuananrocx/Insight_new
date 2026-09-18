/**
 * 分批上传运行器：独立于弹窗生命周期。
 *
 * 弹窗中途关闭（转到后台）时上传循环继续跑完，状态放 store 里，
 * 重新打开弹窗能无缝接上传输进度 / ingest 进度。
 */
import { create } from 'zustand'
import { toast } from 'sonner'

import { api, type SkipMode } from '@/lib/api'

export type RunnerFile = {
  file: File
  relativePath: string
}

const BATCH_MAX_FILES = 10
const BATCH_MAX_BYTES = 64 * 1024 * 1024

type RunnerState = {
  phase: 'idle' | 'uploading' | 'error' | 'finished'
  taskId: string | null
  kbId: string | null
  /** 已完成的批次数 / 总批次数 */
  current: number
  totalBatches: number
  uploadedBytes: number
  totalBytes: number
  /** 出错时卡住的批次索引，retry 从它继续 */
  failedAt: number | null
  error: string | null
  /** 弹窗是否已经展示过本次任务完成态（防止重开弹窗跳到旧任务） */
  seen: boolean
  start: (p: {
    kbId: string
    skipMode: SkipMode
    autoIngest: boolean
    files: RunnerFile[]
  }) => Promise<void>
  retry: () => Promise<void>
  cancelUpload: () => Promise<void>
  markSeen: () => void
}

// 模块级闭包：批次切分结果 + 参数（store 之外，避免序列化 File）
let groups: RunnerFile[][] = []
let params: { kbId: string; skipMode: SkipMode; autoIngest: boolean } | null = null
let cancelRequested = false
let running = false

function splitBatches(files: RunnerFile[]): RunnerFile[][] {
  const out: RunnerFile[][] = []
  let cur: RunnerFile[] = []
  let curBytes = 0
  for (const sf of files) {
    if (
      cur.length >= BATCH_MAX_FILES ||
      (curBytes > 0 && curBytes + sf.file.size > BATCH_MAX_BYTES)
    ) {
      out.push(cur)
      cur = []
      curBytes = 0
    }
    cur.push(sf)
    curBytes += sf.file.size
  }
  if (cur.length) out.push(cur)
  return out
}

async function runBatches(fromIdx: number, set: (s: Partial<RunnerState>) => void, get: () => RunnerState) {
  if (running) return
  running = true
  cancelRequested = false
  try {
    for (let i = fromIdx; i < groups.length; i++) {
      if (cancelRequested) return
      const g = groups[i]
      const isFinal = i === groups.length - 1
      try {
        const res = await api.knowledge.uploadBatch({
          kbId: params!.kbId,
          skipMode: params!.skipMode,
          autoIngest: params!.autoIngest,
          relativePaths: g.map((s) => s.relativePath),
          files: g.map((s) => s.file),
          taskId: get().taskId ?? undefined,
          final: isFinal,
        })
        if (cancelRequested) return
        const st = get()
        set({
          taskId: res.task_id,
          current: i + 1,
          uploadedBytes: st.uploadedBytes + g.reduce((a, s) => a + s.file.size, 0),
          failedAt: null,
          error: null,
        })
        if (res.rejected && res.rejected.length > 0) {
          toast.warning(`后端排除 ${res.rejected.length} 个不支持文件`)
        }
      } catch (e) {
        set({ phase: 'error', failedAt: i, error: (e as Error).message })
        return
      }
    }
    set({ phase: 'finished' })
  } finally {
    running = false
  }
}

export const useUploadRunner = create<RunnerState>((set, get) => ({
  phase: 'idle',
  taskId: null,
  kbId: null,
  current: 0,
  totalBatches: 0,
  uploadedBytes: 0,
  totalBytes: 0,
  failedAt: null,
  error: null,
  seen: false,

  start: async ({ kbId, skipMode, autoIngest, files }) => {
    if (running) return
    groups = splitBatches(files)
    params = { kbId, skipMode, autoIngest }
    set({
      phase: 'uploading',
      taskId: null,
      kbId,
      current: 0,
      totalBatches: groups.length,
      uploadedBytes: 0,
      totalBytes: files.reduce((a, s) => a + s.file.size, 0),
      failedAt: null,
      error: null,
      seen: false,
    })
    await runBatches(0, set, get)
  },

  retry: async () => {
    const st = get()
    if (st.phase !== 'error' || st.failedAt == null) return
    set({ phase: 'uploading', error: null })
    await runBatches(st.failedAt, set, get)
  },

  cancelUpload: async () => {
    cancelRequested = true
    const st = get()
    if (st.taskId) {
      try {
        await api.knowledge.cancelUploadTask(st.taskId)
      } catch {
        // 后端任务可能还没建，忽略
      }
    }
    groups = []
    params = null
    set({
      phase: 'idle',
      taskId: null,
      current: 0,
      totalBatches: 0,
      uploadedBytes: 0,
      totalBytes: 0,
      failedAt: null,
      error: null,
    })
    toast.info('已取消上传')
  },

  markSeen: () => set({ seen: true }),
}))
