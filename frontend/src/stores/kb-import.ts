/**
 * KB Pack 导入的全局运行状态（跨页面存活）：
 * - SSE 连接在 store 层持有，页面切换不断连（断连会被后端判定取消并回滚导入）
 * - 导入完成后自动跟踪该 KB 的文档索引建设（轮询 /indexes），右下角面板持续展示
 * - expanded 控制右下角面板展开/收起（后台运行）
 */
import { create } from 'zustand'
import { toast } from 'sonner'

import { api } from '@/lib/api'

type Phase = 'idle' | 'importing' | 'indexing' | 'done'

type IndexProgress = {
  taskId: string | null
  state: string
  completed: number
  total: number
  failed: number
  detail: string
}

type KbImportState = {
  phase: Phase
  cancelling: boolean
  expanded: boolean
  kbId: string
  kbName: string
  stage: string
  current: number
  total: number
  file: string
  index: IndexProgress | null
  /** false = 已有导入正在进行，拒绝并发 */
  start: (file: File, opts: { newName?: string; forceRebuild: boolean }) => boolean
  cancel: () => void
  expand: () => void
  collapse: () => void
  dismiss: () => void
}

let abortRef: AbortController | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let phaseStartedAt = 0

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

export const useKbImportStore = create<KbImportState>((set, get) => {
  function beginIndexTracking(kbId: string, kbName: string) {
    phaseStartedAt = Date.now()
    set({ phase: 'indexing', kbId, kbName, index: null })
    stopPolling()
    pollTimer = setInterval(async () => {
      try {
        const data = await api.kb.indexes.get(kbId)
        const active = data.tasks.find((t) => ['queued', 'running', 'cancelling'].includes(t.state))
        if (active) {
          set({
            index: {
              taskId: active.id,
              state: active.state,
              completed: active.completed,
              total: active.total,
              failed: active.failed,
              detail: active.detail,
            },
          })
          return
        }
        const latest = data.tasks[0]
        if (!latest && Date.now() - phaseStartedAt < 15000) return // 任务尚未创建，宽限等待
        stopPolling()
        set({ phase: 'done', expanded: false })
        if (latest && ['partial', 'failed'].includes(latest.state)) {
          toast.error(`文档索引建设未完成：${latest.failed} 份失败，可在知识库详情重试`)
        } else {
          toast.success(`知识库就绪：${kbName} 文档索引建设完成`)
        }
      } catch {
        // 轮询失败（如登出态）静默重试
      }
    }, 2000)
  }

  return {
    phase: 'idle',
    cancelling: false,
    expanded: true,
    kbId: '',
    kbName: '',
    stage: '',
    current: 0,
    total: 0,
    file: '',
    index: null,
    start: (file, opts) => {
      if (get().phase === 'importing' || get().phase === 'indexing') {
        toast.error('已有知识库导入正在进行，请等待完成或先取消')
        return false
      }
      const ac = new AbortController()
      abortRef = ac
      stopPolling()
      set({
        phase: 'importing',
        cancelling: false,
        expanded: true,
        kbId: '',
        kbName: opts.newName || '',
        stage: 'starting',
        current: 0,
        total: 0,
        file: '',
        index: null,
      })
      api.kb
        .importPackStream(
          file,
          opts,
          {
            onStage: (d) => set({ total: d.document_count || 0 }),
            onProgress: (d) => {
              set({ stage: d.stage })
              if (typeof d.current === 'number') set({ current: d.current })
              if (typeof d.total === 'number') set({ total: d.total })
              if (d.file) set({ file: d.file })
            },
            onDone: (d) => {
              abortRef = null
              toast.success(`已导入知识库：${d.kb_name}${d.rebuilt ? '（已重建向量）' : ''}`)
              if (d.auto_normalized) {
                setTimeout(() => {
                  toast.info('已自动兼容嵌套 ZIP 结构。下次打包请进入目录内选中文件再压缩。', { duration: 6000 })
                }, 500)
              }
              beginIndexTracking(d.kb_id, d.kb_name)
            },
            onError: (msg) => {
              abortRef = null
              const cancelled = ac.signal.aborted
              set({ phase: 'idle', cancelling: false, stage: '' })
              if (cancelled) {
                toast.info('已取消导入')
              } else {
                toast.error(`导入失败：${msg}`)
              }
            },
          },
          ac.signal,
        )
        .catch((e) => {
          // 流内 error 事件走 onError；这里只兜底网络异常与用户主动取消
          const wasBusy = get().phase === 'importing'
          abortRef = null
          if (wasBusy) {
            set({ phase: 'idle', cancelling: false, stage: '' })
            if (ac.signal.aborted) {
              toast.info('已取消导入')
            } else {
              const msg = e instanceof Error ? e.message : '未知错误'
              toast.error(`导入失败：${msg}`)
            }
          }
        })
      return true
    },
    cancel: () => {
      const s = get()
      if (s.phase === 'importing') {
        set({ cancelling: true })
        abortRef?.abort()
        abortRef = null
      } else if (s.phase === 'indexing' && s.index?.taskId) {
        api.kb.indexes
          .cancel(s.kbId, s.index.taskId)
          .then(() => toast.info('已请求安全取消，已完成的索引保留'))
          .catch((e) => toast.error(`取消失败：${e instanceof Error ? e.message : '未知错误'}`))
      }
    },
    expand: () => set({ expanded: true }),
    collapse: () => set({ expanded: false }),
    dismiss: () => {
      if (get().phase === 'importing' || get().phase === 'indexing') return
      set({ phase: 'idle', index: null })
    },
  }
})
