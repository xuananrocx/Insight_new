import { apiFetch } from './account-api'
import type { QaSource, QaTraceStage, QaStreamCallbacks, QaStreamDone } from './api'

export type AnswerSnapshot = {
  session_id: string; turn_id: string; revision: number
  state: 'running' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  answer: string; sources: QaSource[]; trace: QaTraceStage[]
  started_at: number; finished_at?: number; error?: string; used_provider?: string
}

export const answerJobPath = (sid: string, tid?: string) =>
  `/api/v1/qa/jobs/${encodeURIComponent(sid)}${tid ? `/${encodeURIComponent(tid)}` : ''}`

async function pause(signal?: AbortSignal) {
  if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
  await new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')) }
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve() }, 1000)
    signal?.addEventListener('abort', abort, { once: true })
  })
}

/** Poll complete, revisioned snapshots; reconnect never concatenates duplicate tokens. */
export async function followAnswerJob(body: Record<string, unknown>, cb: QaStreamCallbacks, signal?: AbortSignal, resume = false) {
  const path = answerJobPath(String(body.session_id), String(body.turn_id))
  let created = resume
  let revision = -1
  try {
    for (;;) {
      let response: Response
      try {
        response = await apiFetch(created ? path : '/api/v1/qa/jobs', {
          method: created ? 'GET' : 'POST', signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
          headers: { 'Content-Type': 'application/json' },
          body: created ? undefined : JSON.stringify(body),
        })
      } catch (error) {
        if (signal?.aborted || (error as Error).name === 'AbortError') throw error
        cb.onConnection?.('连接已断开，正在恢复；后台任务不会因断线停止')
        await pause(signal)
        continue
      }
      if (!response.ok) {
        if (response.status >= 500) {
          cb.onConnection?.('服务暂时不可用，正在恢复连接')
          await pause(signal)
          continue
        }
        const error = await response.json().catch(() => ({}))
        throw new Error(typeof error.detail === 'string' ? error.detail : `请求失败 (${response.status})`)
      }
      created = true
      let snapshot: AnswerSnapshot
      try { snapshot = await response.json() }
      catch { await pause(signal); continue }
      cb.onConnection?.('')
      if (snapshot.revision !== revision) {
        revision = snapshot.revision
        cb.onSnapshot?.(snapshot)
      }
      if (snapshot.state === 'done') {
        cb.onDone?.(snapshot as unknown as QaStreamDone)
        return
      }
      if (snapshot.state === 'cancelled') return
      if (snapshot.state !== 'running') {
        cb.onError?.({ message: snapshot.error || '生成已中断', partial: snapshot.answer, trace: snapshot.trace })
        return
      }
      await pause(signal)
    }
  } catch (error) {
    // Detach is not stop. The explicit stop endpoint owns cancellation.
    if ((error as Error).name !== 'AbortError') cb.onError?.({ message: (error as Error).message })
  }
}
