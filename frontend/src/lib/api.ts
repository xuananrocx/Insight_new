const API_BASE = '/api/v1'

// ===== SSE 流解析工具（askStream / importPackStream 共用）=====

/**
 * 解析一个 SSE 帧（block 是不含分隔符 \n\n 的单帧原始文本）。
 * 返回 { event, data } 或 null（无 data 行 / JSON 解析失败）。
 *
 * 兼容 CRLF（/\r?\n/），兼容多行 data:（按 SSE 规范用 \n join）。
 */
function parseSseBlock(block: string): { event: string; data: unknown } | null {
  const lines = block.split(/\r?\n/)
  let evt: string = 'message'
  const dataLines: string[] = []
  for (const ln of lines) {
    if (!ln || ln.startsWith(':')) continue  // 空行或注释
    if (ln.startsWith('event:')) {
      evt = ln.slice(6).trim()
    } else if (ln.startsWith('data:')) {
      // SSE 规范：data: 后面允许有一个空格前缀，需要 strip
      dataLines.push(ln.slice(5).replace(/^ /, ''))
    }
  }
  if (!dataLines.length) return null
  let payload: unknown
  try {
    payload = JSON.parse(dataLines.join('\n'))
  } catch {
    return null
  }
  return { event: evt, data: payload }
}

/**
 * 从 SSE buffer 中按 \n\n（或 \r\n\r\n）切出完整帧；返回剩余未完成的 buffer。
 * onBlock 对每个完整帧调用一次。
 */
function drainSseBuffer(input: string, onBlock: (block: string) => void): string {
  let buffer = input
  while (true) {
    const m = buffer.match(/\r?\n\r?\n/)
    if (!m || m.index === undefined) break
    const block = buffer.slice(0, m.index)
    buffer = buffer.slice(m.index + m[0].length)
    if (block.trim()) onBlock(block)
  }
  return buffer
}

export type KnowledgeStats = {
  files_total: number
  files_done: number
  files_pending: number
  files_failed: number
  total_chunks: number
  total_size_bytes: number
  feedback_pending: number
  feedback_approved: number
}

export type KnowledgeFile = {
  id: number
  relative_path: string
  absolute_path?: string
  content_hash?: string
  file_size: number
  file_type?: string
  source_package?: string | null
  status: string
  chunk_count: number
  error_message?: string | null
  processed_at?: string | null
  created_at?: string
}

export type KnowledgeFilesResponse = {
  files: KnowledgeFile[]
  count: number
}

export type KB = {
  id: string
  name: string
  description: string | null
  collection_name: string
  source: string  // builtin | user | imported
  embedding_model: string | null
  embedding_dim: number | null
  is_default: boolean
  created_at: number
  updated_at: number
  document_count: number
  total_chunks: number
  actual_collection_dim?: number | null
  dim_mismatch?: boolean
}

export type KbSession = {
  id: string
  title: string
  created_at: number
  updated_at: number
  turn_count: number
  kb_scope: string | null
}

// 迭代 4：KB 概念 + 文档关联图
export type KbConcept = {
  id: number
  name: string
  type: string  // error_code | config | concept | component | command | metric | other
  description: string
  mention_count: number
  file_ids: number[]
  file_count: number
}

export type KbGraphNode = {
  file_id: number
  name: string
  concept_count: number
  concepts: string[]
}

export type KbGraphEdge = {
  source: number
  target: number
  shared_count: number
  shared_concepts: string[]
}

export type KbImportPrecheck = {
  check_only: true
  kb_name: string
  kb_description: string
  source_embedding_model: string | null
  source_embedding_dim: number | null
  target_embedding_model: string
  target_embedding_dim: number
  compatible: boolean
  document_count: number
  total_chunks: number
  is_name_duplicate: boolean
  suggested_name: string
}

export type KbImportResult = {
  check_only: false
  imported: boolean
  needs_rebuild?: boolean
  ready_to_stream?: boolean
  kb_id?: string
  rebuilt?: boolean
  kb_name: string
  kb_description: string
  source_embedding_model: string | null
  source_embedding_dim: number | null
  target_embedding_model: string
  target_embedding_dim: number
  compatible: boolean
  document_count: number
  total_chunks: number
}

// SSE 事件 data 类型
export type KbImportStageData = {
  stage: 'starting'
  compatible: boolean
  will_rebuild: boolean
  document_count: number
  total_chunks: number
}

export type KbImportProgressData = {
  stage: 'creating_kb' | 'extracting_documents' | 'importing_vectors' | 'rebuilding_embeddings' | 'done'
  current?: number
  total?: number
  file?: string
  kb_name?: string
  kb_id?: string
}

export type KbImportDoneData = {
  kb_id: string
  kb_name: string
  rebuilt: boolean
  auto_normalized?: boolean
}

export type CreateKBRequest = {
  name: string
  description: string
}

export type UpdateKBRequest = {
  name?: string
  description?: string
}

export type EmbeddingModel = {
  name: string
  label: string
  size: string
  dimensions: number
  description: string
  is_active: boolean
  is_cached?: boolean
}

export type DimMismatchedKb = {
  kb_id: string
  name: string
  collection_name: string
  declared_dim: number | null
  actual_dim: number
  chunk_count: number
}

export type EmbeddingStatus = {
  mode: string
  current_model: string
  dimensions: number
  cache_size?: number
  cached_models?: string[]
  available_models: EmbeddingModel[]
  dim_mismatched_kbs?: DimMismatchedKb[]
}

export type QaSource = {
  file_path?: string
  rel_path?: string
  chunk_index?: number
  content: string
  score?: number
  [k: string]: unknown
}

export type QaTraceCandidate = {
  title?: string | null
  source_name?: string | null
  score?: number | null
  score_type?: 'cosine' | 'bm25' | 'rrf' | 'rerank' | string
  preview?: string | null
}

export type QaTraceStage = {
  stage: string
  label: string
  count?: number
  duration_ms?: number
  status?: 'ok' | 'partial' | 'skipped' | 'failed' | 'empty' | string
  notes?: string
  candidates?: QaTraceCandidate[]
}

export type QaAnswer = {
  answer: string
  sources?: QaSource[]
  used_provider?: string
  used_model?: string
  trace?: QaTraceStage[]
}

export type QaStreamDone = {
  answer: string
  trace: QaTraceStage[]
  sources: QaSource[]
  used_provider?: string
  used_chunks?: number
}

export type QaStreamCallbacks = {
  onWarmup?: (data: { step: string; msg: string }) => void
  onStage?: (stage: QaTraceStage) => void
  onSources?: (sources: QaSource[]) => void
  onToken?: (token: string) => void
  onDone?: (data: QaStreamDone) => void
  onError?: (data: { message: string; partial?: string; rebuildInProgress?: boolean }) => void
  onCancelled?: () => void
}

export type ChatMessage = {
  role: 'user' | 'assistant' | 'system'
  content: string
}

export type QaAskOptions = {
  topK?: number
  history?: ChatMessage[]
  kbScope?: string
}

export type FeedbackItem = {
  id: number
  question: string
  answer: string
  sources_json: string
  used_provider?: string
  rating: number
  user_comment?: string
  status: string
  reviewer?: string | null
  review_note?: string | null
  reviewed_at?: string | null
  knowledge_file_id?: number | null
  created_at?: string
}

// ===== 批量上传（v8+）=====

export type UploadTaskStatus = 'running' | 'paused' | 'completed' | 'cancelled' | 'failed'
export type UploadFileStatus = 'queued' | 'processing' | 'done' | 'skipped' | 'failed' | 'cancelled'
export type SkipMode = 'skip' | 'overwrite'

export type UploadTask = {
  id: string
  kb_id: string
  skip_mode: SkipMode
  auto_ingest: number  // 0/1（SQLite 存 INTEGER）
  total: number
  done: number
  skipped: number
  failed: number
  status: UploadTaskStatus
  current_file_path: string | null
  current_stage: string | null
  created_at: number
  updated_at: number
  finished_at: number | null
  // 由 /upload_tasks/active 端点额外补的计数字段
  queued_count?: number
  processing_count?: number
}

export type UploadTaskFile = {
  id: number
  task_id: string
  relative_path: string
  absolute_path: string
  file_size: number
  status: UploadFileStatus
  skip_reason: string | null
  error_message: string | null
  started_at: number | null
  finished_at: number | null
}

export type UploadBatchResponse = {
  task_id: string
  total: number
  rejected?: Array<{ relative_path: string; reason: string }>
}

export type SupportedExtensionsResponse = {
  extensions: string[]
}

export type FileProgressEvent = {
  task_id: string
  file_id: number
  relative_path: string
  stage: 'parsing' | 'chunking' | 'embedding' | 'writing' | 'ai_summary' | 'done'
  percent: number
  detail?: string
}

export type UploadTaskDetail = UploadTask & { files: UploadTaskFile[] }

export type BatchUploadStreamCallbacks = {
  onSnapshot?: (snapshot: UploadTask) => void
  onFileStarted?: (e: {
    task_id: string
    file_id: number
    relative_path: string
  }) => void
  onFileProgress?: (e: FileProgressEvent) => void
  onFileFinished?: (e: {
    task_id: string
    file_id: number
    relative_path: string
    status: UploadFileStatus
    skip_reason: string | null
    error: string | null
    done: number
    skipped: number
    failed: number
    total: number
    task_finished: boolean
  }) => void
  onTaskCompleted?: (e: {
    task_id: string
    total: number
    done: number
    skipped: number
    failed: number
  }) => void
  onTaskCancelled?: (e: { task_id: string }) => void
  onTaskCrashed?: (e: { task_id: string }) => void
  onError?: (message: string) => void
  onCancelled?: () => void
}

export type FilePathListResponse = {
  paths: string[]
  count: number
}

export type UploadTasksActiveResponse = {
  tasks: UploadTask[]
  count: number
}

// ===== AI 调用日志 =====

export type AiScene = 'qa_chat' | 'summarize' | 'concept_extract' | 'title' | 'test'

export type AiCallLogListItem = {
  id: number
  provider: string
  model: string | null
  scene: AiScene | string
  session_id: string | null
  turn_id: string | null
  kb_id: string | null
  duration_ms: number | null
  success: number  // 0/1
  error_message: string | null
  token_input: number | null
  token_output: number | null
  created_at: number
  messages_size: number
  response_size: number | null
}

export type AiCallLogDetail = AiCallLogListItem & {
  system_prompt: string | null
  messages: Array<{ role: string; content: string }>
  response_text: string | null
}

export type AiCallLogListResponse = {
  items: AiCallLogListItem[]
  total: number
  limit: number
  offset: number
}

export type AiCallLogStats = {
  total: number
  success: number
  failed: number
  avg_duration_ms: number
  by_provider: Array<{
    provider: string
    total: number
    success: number
    failed: number
    avg_duration_ms: number
  }>
  by_scene: Array<{
    scene: string
    total: number
    success: number
    failed: number
    avg_duration_ms: number
  }>
  by_day: Array<{ date: string; total: number; success: number; failed: number }>
}

export type AiCallLogConfig = {
  enabled: boolean
  log_response: boolean
  log_system_prompt: boolean
  log_messages: boolean
  retention_days: number
  scenes: Record<string, boolean>
}

export type UploadBatchParams = {
  kbId: string
  skipMode: SkipMode
  autoIngest: boolean
  /** 跟 files 一一对应的相对路径（保留子目录结构）；不传则用 file.name */
  relativePaths?: string[]
  files: File[]
}

export type FeedbackListResponse = {
  items: FeedbackItem[]
  count: number
}

export type FeedbackCreateBody = {
  question: string
  answer: string
  rating?: number
  sources?: QaSource[]
  used_provider?: string
  user_comment?: string
}

export type FeedbackCreateResponse = {
  ok: boolean
  feedback_id: number
  message?: string
}

function parseSources(json: string): QaSource[] {
  if (!json) return []
  try {
    const parsed = JSON.parse(json)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function parseFeedbackList(d: FeedbackListResponse): FeedbackItem[] {
  // 直接透传后端字段；sources_json 在使用时通过 getFeedbackSources 解析。
  // 不再注入 __sources 字段（黑魔法），保持 FeedbackItem 类型纯净。
  return (d.items ?? []).slice()
}

async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const { timeoutMs = 30_000, ...rest } = init ?? {}
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(timeoutMs),
      ...rest,
    })
  } catch (e: unknown) {
    // 区分超时和真实网络错误，统一中文化
    if (e instanceof DOMException && e.name === 'TimeoutError') {
      throw new Error(`请求超时（${Math.round(timeoutMs / 1000)}秒）`)
    }
    if (e instanceof Error && e.name === 'AbortError') {
      throw new Error('请求被取消')
    }
    throw new Error(`网络错误：${e instanceof Error ? e.message : String(e)}`)
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? body.message ?? JSON.stringify(body)
      if (Array.isArray(detail)) {
        detail = detail.map((e: { msg?: string; message?: string }) => e.msg ?? e.message ?? '').join('; ')
      }
    } catch {
      // ignore
    }
    throw new Error(`${res.status}: ${detail}`)
  }
  if (res.status === 204) return null as T
  return res.json() as Promise<T>
}

export const api = {
  knowledge: {
    stats: (kbId?: string) =>
      request<KnowledgeStats>(
        `/knowledge/stats${kbId ? `?kb_id=${encodeURIComponent(kbId)}` : ''}`,
      ),
    files: async (kbId?: string): Promise<KnowledgeFile[]> => {
      const d = await request<KnowledgeFilesResponse>(
        `/knowledge/files${kbId ? `?kb_id=${encodeURIComponent(kbId)}` : ''}`,
      )
      return d.files ?? []
    },
    scan: (kbId?: string) =>
      request<{ message: string; stats: KnowledgeStats }>(
        `/knowledge/scan${kbId ? `?kb_id=${encodeURIComponent(kbId)}` : ''}`,
        { method: 'POST', timeoutMs: 5 * 60_000 },
      ),
    supportedTypes: () => request<Record<string, string[]>>('/knowledge/supported-types'),
    remove: (relPath: string, kbId?: string) => {
      const qs = kbId ? `?kb_id=${encodeURIComponent(kbId)}` : ''
      return request<{ message: string }>(`/knowledge/files/${encodeURIComponent(relPath)}${qs}`, {
        method: 'DELETE',
      })
    },
    upload: async (file: File, kbId?: string) => {
      const form = new FormData()
      form.append('file', file)
      const qs = kbId ? `?kb_id=${encodeURIComponent(kbId)}` : ''
      const res = await fetch(`${API_BASE}/knowledge/upload${qs}`, {
        method: 'POST',
        body: form,
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(`${res.status}: ${text}`)
      }
      return res.json()
    },

    // ===== 批量上传（v8+）=====

    filePaths: (kbId: string) =>
      request<FilePathListResponse>(
        `/knowledge/file_paths?kb_id=${encodeURIComponent(kbId)}`,
      ),

    supportedExtensions: () =>
      request<SupportedExtensionsResponse>('/knowledge/supported_extensions'),

    uploadBatch: async (params: UploadBatchParams): Promise<UploadBatchResponse> => {
      const form = new FormData()
      form.append('kb_id', params.kbId)
      form.append('skip_mode', params.skipMode)
      form.append('auto_ingest', String(params.autoIngest))
      // relative_paths 数组跟 files 一一对应
      if (params.relativePaths && params.relativePaths.length === params.files.length) {
        params.relativePaths.forEach((rp) => form.append('relative_paths', rp))
      }
      params.files.forEach((f) => form.append('files', f))

      const res = await fetch(`${API_BASE}/knowledge/upload_batch`, {
        method: 'POST',
        body: form,
        signal: AbortSignal.timeout(10 * 60_000),  // 上传大文件可以慢
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(`${res.status}: ${text}`)
      }
      return res.json()
    },

    uploadTasksActive: () =>
      request<UploadTasksActiveResponse>('/knowledge/upload_tasks/active'),

    uploadTask: (taskId: string, filesStatus?: UploadFileStatus) => {
      const qs = filesStatus ? `?files_status=${filesStatus}` : ''
      return request<UploadTaskDetail>(`/knowledge/upload_tasks/${taskId}${qs}`)
    },

    retryUploadTask: (taskId: string, fileIds?: number[]) =>
      request<{ ok: boolean; retried_count: number }>(
        `/knowledge/upload_tasks/${taskId}/retry`,
        {
          method: 'POST',
          body: JSON.stringify(fileIds ?? []),
        },
      ),

    deleteTaskFiles: (
      taskId: string,
      status: 'failed' | 'skipped' | 'cancelled' = 'failed',
      fileIds?: number[],
    ) =>
      request<{ ok: boolean; deleted_count: number }>(
        `/knowledge/upload_tasks/${taskId}/files?status=${status}`,
        {
          method: 'DELETE',
          body: JSON.stringify(fileIds ?? []),
        },
      ),

    cancelUploadTask: (taskId: string) =>
      request<{ ok: boolean }>(`/knowledge/upload_tasks/${taskId}/cancel`, {
        method: 'POST',
      }),

    resumeUploadTask: (taskId: string) =>
      request<{ ok: boolean }>(`/knowledge/upload_tasks/${taskId}/resume`, {
        method: 'POST',
      }),

    deleteUploadTask: (taskId: string) =>
      request<{ ok: boolean }>(`/knowledge/upload_tasks/${taskId}`, {
        method: 'DELETE',
      }),

    streamUploadTask: async (
      taskId: string,
      cb: BatchUploadStreamCallbacks,
      signal?: AbortSignal,
    ): Promise<void> => {
      try {
        const res = await fetch(
          `${API_BASE}/knowledge/upload_tasks/${taskId}/stream`,
          { method: 'GET', signal },
        )
        if (!res.ok || !res.body) {
          let detail = res.statusText
          try {
            const body = await res.json()
            detail = body.detail ?? body.message ?? JSON.stringify(body)
          } catch {
            // ignore
          }
          throw new Error(`${res.status}: ${detail}`)
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        const dispatchBlock = (block: string) => {
          const parsed = parseSseBlock(block)
          if (!parsed) return
          const { event: evtType, data: payload } = parsed
          const p = payload as Record<string, unknown>
          switch (evtType) {
            case 'snapshot':
              cb.onSnapshot?.(p as unknown as UploadTask)
              break
            case 'file_started':
              cb.onFileStarted?.(
                p as unknown as {
                  task_id: string
                  file_id: number
                  relative_path: string
                },
              )
              break
            case 'file_progress':
              cb.onFileProgress?.(p as unknown as FileProgressEvent)
              break
            case 'file_finished':
              cb.onFileFinished?.(
                p as unknown as {
                  task_id: string
                  file_id: number
                  relative_path: string
                  status: UploadFileStatus
                  skip_reason: string | null
                  error: string | null
                  done: number
                  skipped: number
                  failed: number
                  total: number
                  task_finished: boolean
                },
              )
              break
            case 'task_completed':
              cb.onTaskCompleted?.(
                p as unknown as {
                  task_id: string
                  total: number
                  done: number
                  skipped: number
                  failed: number
                },
              )
              break
            case 'task_cancelled':
              cb.onTaskCancelled?.(p as unknown as { task_id: string })
              break
            case 'task_crashed':
              cb.onTaskCrashed?.(p as unknown as { task_id: string })
              break
            case 'error':
              cb.onError?.((p as { message?: string }).message ?? '未知错误')
              break
          }
        }

        try {
          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            buffer = drainSseBuffer(buffer, dispatchBlock)
          }
          if (buffer.trim()) {
            dispatchBlock(buffer.trim())
          }
        } finally {
          try {
            reader.releaseLock()
          } catch {
            // ignore
          }
        }
      } catch (e: unknown) {
        if (e instanceof Error && e.name === 'AbortError') {
          cb.onCancelled?.()
          return
        }
        throw e
      }
    },
  },

  kb: {
    list: () => request<KB[]>('/kbs'),
    get: (id: string) => request<KB>(`/kbs/${id}`),
    create: (req: CreateKBRequest) =>
      request<KB>('/kbs', {
        method: 'POST',
        body: JSON.stringify(req),
      }),
    update: (id: string, req: UpdateKBRequest) =>
      request<KB>(`/kbs/${id}`, {
        method: 'PUT',
        body: JSON.stringify(req),
      }),
    delete: (id: string) =>
      request<{ deleted: boolean; kb_id: string }>(`/kbs/${id}`, {
        method: 'DELETE',
      }),
    rebuild: (id: string) =>
      request<{ task_id: string | null; total: number; new_dim?: number | null; message?: string }>(
        `/kbs/${id}/rebuild?confirm=true`,
        { method: 'POST' },
      ),
    deleteFailedFiles: (id: string) =>
      request<{ ok: boolean; deleted_count: number; kb_id: string }>(
        `/kbs/${id}/delete_failed_files?confirm=true`,
        { method: 'POST' },
      ),
    // 查 KB 绑定的会话列表
    sessions: (id: string) =>
      request<{ sessions: KbSession[]; count: number }>(`/kbs/${id}/sessions`),
    // KB 概念列表（迭代 4）
    concepts: (id: string, minMention = 1) =>
      request<{
        kb_id: string
        concepts: KbConcept[]
        count: number
      }>(`/kbs/${id}/concepts?min_mention=${minMention}`),
    // KB 文档关联图（迭代 4）
    documentGraph: (id: string, minShared = 1) =>
      request<{
        kb_id: string
        nodes: KbGraphNode[]
        edges: KbGraphEdge[]
        concepts_count: number
      }>(`/kbs/${id}/document_graph?min_shared=${minShared}`),
    // KB 全局摘要（迭代 6）
    globalSummary: {
      get: (id: string) =>
        request<{
          kb_id: string
          has_summary: boolean
          summary: string | null
          model: string | null
          tokens: number | null
          created_at: number | null
          doc_count: number | null
        }>(`/kbs/${id}/global_summary`),
      build: (id: string, force = false) =>
        request<{
          kb_id: string
          has_summary: boolean
          summary: string | null
          model: string | null
          tokens: number | null
          created_at: number | null
          doc_count: number | null
        }>(`/kbs/${id}/global_summary/build?force=${force}`, { method: 'POST' }),
      remove: (id: string) =>
        request<{ deleted: boolean; kb_id: string }>(`/kbs/${id}/global_summary`, {
          method: 'DELETE',
        }),
    },
    // 导出 KB：直接触发浏览器下载（返回 ZIP）
    exportUrl: (id: string) => `${API_BASE}/kbs/${id}/export`,
    // 导入 KB Pack
    precheckImport: async (file: File): Promise<KbImportPrecheck> => {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`${API_BASE}/kbs/import?check_only=true`, {
        method: 'POST',
        body: form,
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(`${res.status}: ${text}`)
      }
      return res.json()
    },
    importPack: async (
      file: File,
      opts: { newName?: string; forceRebuild?: boolean } = {},
    ): Promise<KbImportResult> => {
      const form = new FormData()
      form.append('file', file)
      const params = new URLSearchParams()
      if (opts.newName) params.set('new_name', opts.newName)
      if (opts.forceRebuild) params.set('force_rebuild', 'true')
      const qs = params.toString() ? `?${params.toString()}` : ''
      const res = await fetch(`${API_BASE}/kbs/import${qs}`, {
        method: 'POST',
        body: form,
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(`${res.status}: ${text}`)
      }
      return res.json()
    },
    // SSE 流式导入：实时回调 stage/progress/done/error
    importPackStream: async (
      file: File,
      opts: { newName?: string; forceRebuild?: boolean },
      cb: {
        onStage?: (data: KbImportStageData) => void
        onProgress?: (data: KbImportProgressData) => void
        onDone?: (data: KbImportDoneData) => void
        onError?: (msg: string) => void
      },
      signal?: AbortSignal,
    ): Promise<void> => {
      const form = new FormData()
      form.append('file', file)
      const params = new URLSearchParams()
      if (opts.newName) params.set('new_name', opts.newName)
      if (opts.forceRebuild) params.set('force_rebuild', 'true')
      const qs = params.toString() ? `?${params.toString()}` : ''
      const res = await fetch(`${API_BASE}/kbs/import_stream${qs}`, {
        method: 'POST',
        body: form,
        signal,
      })
      if (!res.ok || !res.body) {
        let detail = res.statusText
        try {
          const body = await res.json()
          detail = body.detail ?? body.message ?? JSON.stringify(body)
        } catch {
          // ignore
        }
        throw new Error(`${res.status}: ${detail}`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      const dispatchBlock = (block: string) => {
        const parsed = parseSseBlock(block)
        if (!parsed) return
        const { event: evt, data } = parsed
        if (evt === 'stage') cb.onStage?.(data as unknown as KbImportStageData)
        else if (evt === 'progress') cb.onProgress?.(data as unknown as KbImportProgressData)
        else if (evt === 'done') cb.onDone?.(data as unknown as KbImportDoneData)
        else if (evt === 'error') cb.onError?.((data as { message?: string }).message ?? '未知错误')
        else if (evt === 'cancelled') cb.onError?.('已取消')
      }

      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        buffer = drainSseBuffer(buffer, dispatchBlock)
      }
      // 流结束时的残余 buffer：尝试当一帧解析（后端最后一段可能没补 \n\n）
      if (buffer.trim()) {
        dispatchBlock(buffer.trim())
      }
    },
  },

  qa: {
    ask: (question: string, opts: QaAskOptions = {}) =>
      request<QaAnswer>('/qa/ask', {
        method: 'POST',
        body: JSON.stringify({
          question,
          top_k: opts.topK ?? 5,
          history: opts.history,
          kb_scope: opts.kbScope,
        }),
      }),
    askStream: async (
      question: string,
      history: ChatMessage[] | undefined,
      cb: QaStreamCallbacks,
      signal?: AbortSignal,
      kbScope?: string,
      sessionId?: string,
      turnId?: string,
    ): Promise<void> => {
     try {
      const res = await fetch(`${API_BASE}/qa/ask_stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          history,
          kb_scope: kbScope,
          session_id: sessionId,
          turn_id: turnId,
        }),
        signal,
      })
      if (!res.ok || !res.body) {
        let detail = res.statusText
        try {
          const body = await res.json()
          detail = body.detail ?? body.message ?? JSON.stringify(body)
        } catch {
          // ignore
        }
        // U-2: 503 (rebuilding) 友好提示
        if (res.status === 503) {
          // 释放 body stream（避免连接泄漏）
          try {
            await res.body?.cancel()
          } catch {
            // ignore
          }
          cb.onError?.({
            message: '向量库重建中，请等待完成后再提问',
            rebuildInProgress: true,
          })
          return
        }
        throw new Error(`${res.status}: ${detail}`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      const dispatchBlock = (block: string) => {
        const parsed = parseSseBlock(block)
        if (!parsed) return
        const { event: evtType, data: payload } = parsed
        switch (evtType) {
          case 'warmup':
            cb.onWarmup?.(payload as { step: string; msg: string })
            break
          case 'stage':
            cb.onStage?.(payload as QaTraceStage)
            break
          case 'sources':
            cb.onSources?.((payload as QaSource[]) || [])
            break
          case 'token':
            cb.onToken?.((payload as { text?: string })?.text ?? '')
            break
          case 'done':
            cb.onDone?.(payload as QaStreamDone)
            break
          case 'error':
            cb.onError?.(
              payload as { message: string; partial?: string; rebuildInProgress?: boolean },
            )
            break
        }
      }

      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          buffer = drainSseBuffer(buffer, dispatchBlock)
        }
        // 流结束时的残余 buffer：尝试当一帧解析（parseSseBlock 已兼容多行 data:）
        if (buffer.trim()) {
          dispatchBlock(buffer.trim())
        }
      } finally {
        try {
          reader.releaseLock()
        } catch {
          // ignore
        }
      }
     } catch (e: any) {
      // U-1: 区分 AbortError（用户主动取消）vs 真实网络错误
      if (e?.name === 'AbortError') {
        cb.onCancelled?.()
        return
      }
      // 已经被上面的 503 路径处理过 onError，不重复
      if (e?.message?.startsWith('503:')) return
      cb.onError?.({ message: e?.message || '网络错误' })
     }
    },
    summarizeTitle: (question: string, answer: string) =>
      request<{ title: string }>('/qa/summarize-title', {
        method: 'POST',
        body: JSON.stringify({ question, answer }),
        timeoutMs: 90_000,  // 调 LLM，可能慢
      }),
  },

  feedback: {
    create: (payload: FeedbackCreateBody) =>
      request<FeedbackCreateResponse>('/feedback', {
        method: 'POST',
        // rating 不设默认：调用方应明确传值（点赞=1/点踩=-1/未评分=0）。
        // 如果未传，后端按 0 处理（neutral）。
        body: JSON.stringify(payload),
      }),
    pending: async (): Promise<FeedbackItem[]> => {
      const d = await request<FeedbackListResponse>('/feedback/pending')
      return parseFeedbackList(d)
    },
    approved: async (): Promise<FeedbackItem[]> => {
      const d = await request<FeedbackListResponse>('/feedback/approved')
      return parseFeedbackList(d)
    },
    review: (id: number | string, decision: 'approved' | 'rejected', note = '', reviewer = 'pm') =>
      request<{ ok: boolean; feedback_id: number }>(`/feedback/${id}/review`, {
        method: 'POST',
        body: JSON.stringify({ decision, note, reviewer }),
      }),
    parseSources,
  },

  embedding: {
    status: () => request<EmbeddingStatus>('/embedding/status'),
    switch: (modelName: string) =>
      request<EmbeddingStatus>('/embedding/switch', {
        method: 'POST',
        body: JSON.stringify({ model_name: modelName }),
      }),
    precheck: (modelName: string) =>
      request<EmbeddingPrecheckResult>('/embedding/precheck', {
        method: 'POST',
        body: JSON.stringify({ model_name: modelName }),
      }),
    rebuildAndSwitch: (modelName: string) =>
      request<{ status: string; model_name: string; message: string }>('/embedding/rebuild-and-switch', {
        method: 'POST',
        body: JSON.stringify({ model_name: modelName }),
      }),
    rebuildStatus: () => request<EmbeddingRebuildStatus>('/embedding/rebuild/status'),
    setCacheSize: (cacheSize: number) =>
      request<EmbeddingStatus>('/embedding/cache-config', {
        method: 'POST',
        body: JSON.stringify({ cache_size: cacheSize }),
      }),
  },

  prompt: {
    get: () => request<SystemPromptInfo>('/settings/system_prompt'),
    update: (systemPrompt: string) =>
      request<SystemPromptInfo>('/settings/system_prompt', {
        method: 'PUT',
        body: JSON.stringify({ system_prompt: systemPrompt }),
      }),
    reset: () =>
      request<SystemPromptInfo>('/settings/system_prompt/reset', {
        method: 'POST',
      }),
    test: (systemPrompt: string, question: string) =>
      request<SystemPromptTestResult>('/settings/system_prompt/test', {
        method: 'POST',
        body: JSON.stringify({ system_prompt: systemPrompt, question }),
        timeoutMs: 90_000,
      }),
  },

  llm: {
    providers: () => request<LLMProvidersResponse>('/settings/llm_providers'),
    switch: (providerName: string) =>
      request<LLMProvidersResponse>('/settings/llm_providers/switch', {
        method: 'POST',
        body: JSON.stringify({ provider_name: providerName }),
      }),
    test: (providerName: string) =>
      request<{ success: boolean; error?: string; provider: string }>('/settings/llm_providers/test', {
        method: 'POST',
        body: JSON.stringify({ provider_name: providerName }),
        timeoutMs: 90_000,
      }),
    testProvider: (config: { name: string; base_url?: string; api_key?: string; model?: string }) =>
      request<{ success: boolean; error?: string; provider: string }>('/settings/llm_providers/test_config', {
        method: 'POST',
        body: JSON.stringify(config),
        timeoutMs: 90_000,
      }),
    update: (config: { name: string; base_url?: string; api_key?: string; model?: string; enabled?: boolean }) =>
      request<{ name: string }>('/settings/llm_providers/update', {
        method: 'POST',
        body: JSON.stringify(config),
      }),
  },

  retrieval: {
    get: (kbId: string) =>
      request<{ kb_id: string; strategy: string; available_strategies: string[] }>(
        `/settings/retrieval_strategy?kb_id=${encodeURIComponent(kbId)}`,
      ),
    update: (kbId: string, strategy: 'basic' | 'summary' | 'agentic') =>
      request<{ kb_id: string; strategy: string; available_strategies: string[] }>(
        '/settings/retrieval_strategy',
        {
          method: 'PUT',
          body: JSON.stringify({ kb_id: kbId, strategy }),
        },
      ),
  },

  aiSummary: {
    get: () =>
      request<{ enabled: boolean; min_word_count: number }>('/settings/ai_summary'),
    update: (config: { enabled: boolean; min_word_count: number }) =>
      request<{ enabled: boolean; min_word_count: number }>('/settings/ai_summary', {
        method: 'PUT',
        body: JSON.stringify(config),
      }),
  },

  defaultKb: {
    get: () =>
      request<{
        kb_id: string | null
        name: string | null
        source: 'config' | 'is_default'
      }>('/settings/default_kb'),
    update: (kbId: string | null) =>
      request<{
        kb_id: string | null
        name: string | null
        source: 'config' | 'is_default'
      }>('/settings/default_kb', {
        method: 'PUT',
        body: JSON.stringify({ kb_id: kbId }),
      }),
  },

  sessions: {
    list: () => request<SessionSummary[]>('/sessions'),
    get: (id: string) => request<SessionDetail>(`/sessions/${encodeURIComponent(id)}`),
    create: (payload: { id: string; title: string; created_at: number; kb_scope?: string }) =>
      request<SessionSummary>('/sessions', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    update: (id: string, payload: { title?: string; kb_scope?: string }) =>
      request<SessionSummary>(`/sessions/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
      }),
    remove: (id: string) =>
      request<void>(`/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    addTurn: (
      sessionId: string,
      payload: {
        id: string
        question: string
        answer?: string
        sources?: QaSource[]
        trace?: QaTraceStage[]
        used_provider?: string
        liked?: boolean
        error?: string
        created_at: number
      },
    ) =>
      request<PersistedTurn>(`/sessions/${encodeURIComponent(sessionId)}/turns`, {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    patchTurn: (
      sessionId: string,
      turnId: string,
      payload: {
        answer?: string
        sources?: QaSource[]
        trace?: QaTraceStage[]
        used_provider?: string
        liked?: boolean
        error?: string
        updated_at?: number
      },
    ) =>
      request<{ ok: boolean }>(`/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
      }),
    deleteTurn: (sessionId: string, turnId: string) =>
      request<void>(`/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}`, {
        method: 'DELETE',
      }),
    clearAllTurns: (sessionId: string) =>
      request<{ deleted: number }>(`/sessions/${encodeURIComponent(sessionId)}/turns`, {
        method: 'DELETE',
      }),
    // 导出单会话 → 返回 Blob（含 Content-Disposition 文件名）
    exportOne: async (id: string): Promise<{ blob: Blob; filename: string }> => {
      const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(id)}/export`)
      if (!res.ok) throw new Error(`导出失败: HTTP ${res.status}`)
      const blob = await res.blob()
      const cd = res.headers.get('content-disposition') || ''
      // 优先解析 filename*=UTF-8''...，否则用 filename="..."
      let filename = 'session.json'
      const star = cd.match(/filename\*=UTF-8''([^;]+)/i)
      if (star?.[1]) filename = decodeURIComponent(star[1])
      else {
        const m = cd.match(/filename="?([^";]+)"?/i)
        if (m?.[1]) filename = m[1]
      }
      return { blob, filename }
    },
    // 批量导出 → zip Blob
    exportBatch: async (ids: string[]): Promise<{ blob: Blob; filename: string }> => {
      const res = await fetch(`${API_BASE}/sessions/export-batch`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ ids }),
      })
      if (!res.ok) throw new Error(`批量导出失败: HTTP ${res.status}`)
      const blob = await res.blob()
      const cd = res.headers.get('content-disposition') || ''
      let filename = 'sessions.zip'
      const star = cd.match(/filename\*=UTF-8''([^;]+)/i)
      if (star?.[1]) filename = decodeURIComponent(star[1])
      else {
        const m = cd.match(/filename="?([^";]+)"?/i)
        if (m?.[1]) filename = m[1]
      }
      return { blob, filename }
    },
    // 导入会话（json 或 zip）
    importFile: async (file: File): Promise<{
      imported: number
      sessions: { id: string; title: string; turn_count: number; created_at: number }[]
      errors: string[]
    }> => {
      const fd = new FormData()
      fd.append('file', file)
      const res = await fetch(`${API_BASE}/sessions/import`, {
        method: 'POST',
        body: fd,
      })
      if (!res.ok) {
        const txt = await res.text().catch(() => '')
        throw new Error(`导入失败: HTTP ${res.status} ${txt}`)
      }
      return res.json()
    },
  },

  logs: {
    info: () => request<LogsInfo>('/logs/info'),
    tail: (lines = 200) => request<{ lines: string[] }>(`/logs/tail?lines=${lines}`),
    clear: () => request<void>('/logs', { method: 'DELETE' }),
    downloadUrl: () => `${API_BASE}/logs/download`,
  },

  logLevel: {
    get: () => request<LogLevelInfo>('/settings/log_level'),
    update: (level: string) =>
      request<LogLevelInfo>('/settings/log_level', {
        method: 'PUT',
        body: JSON.stringify({ level }),
      }),
  },

  aiLogs: {
    list: (params: {
      provider?: string
      scene?: string
      success?: boolean
      session_id?: string
      kb_id?: string
      start_ts?: number
      end_ts?: number
      limit?: number
      offset?: number
    } = {}) => {
      const qs = new URLSearchParams()
      Object.entries(params).forEach(([k, v]) => {
        if (v != null) qs.append(k, String(v))
      })
      const queryStr = qs.toString() ? `?${qs.toString()}` : ''
      return request<AiCallLogListResponse>(`/ai_logs${queryStr}`)
    },
    detail: (id: number) => request<AiCallLogDetail>(`/ai_logs/${id}`),
    delete: (id: number) =>
      request<{ ok: boolean }>(`/ai_logs/${id}`, { method: 'DELETE' }),
    batchDelete: (body: { before_ts?: number; provider?: string; scene?: string }) =>
      request<{ ok: boolean; deleted: number }>(`/ai_logs/batch_delete`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    cleanup: () =>
      request<{ ok: boolean; deleted: number; retention_days: number }>(
        `/ai_logs/cleanup`,
        { method: 'POST' },
      ),
    deleteAll: () =>
      request<{ ok: boolean; deleted: number }>(`/ai_logs/delete_all`, {
        method: 'POST',
      }),
    stats: (params: { start_ts?: number; end_ts?: number } = {}) => {
      const qs = new URLSearchParams()
      Object.entries(params).forEach(([k, v]) => {
        if (v != null) qs.append(k, String(v))
      })
      const queryStr = qs.toString() ? `?${qs.toString()}` : ''
      return request<AiCallLogStats>(`/ai_logs/stats${queryStr}`)
    },
    getConfig: () => request<AiCallLogConfig>('/ai_logs/config'),
    updateConfig: (body: Partial<{
      enabled: boolean
      log_response: boolean
      log_system_prompt: boolean
      log_messages: boolean
      retention_days: number
      scenes: Record<string, boolean>
    }>) =>
      request<{ ok: boolean; config: AiCallLogConfig }>(`/ai_logs/config`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
  },
}

export type EmbeddingPrecheckResult = {
  needs_rebuild: boolean
  current_dim: number
  new_dim: number
  doc_count: number
  est_seconds: number
}

export type EmbeddingRebuildStatus = {
  status: 'idle' | 'in_progress' | 'succeeded' | 'failed'
  model_name: string | null
  started_at: number | null
  completed_at: number | null
  failed_at: number | null
  current: number
  total: number
  current_file: string
  stage: string
  error: string | null
}

export type SystemPromptInfo = {
  current: string
  default: string
  is_default: boolean
  max_length: number
}

export type SystemPromptTestSource = {
  source_path: string
  source_name: string
  title: string
  section_label: string
  text_snippet: string
  score: number
}

export type SystemPromptTestResult = {
  answer: string
  sources: SystemPromptTestSource[]
  used_provider: string
}

export type LLMProviderInfo = {
  name: string
  enabled: boolean
  has_chat: boolean
  has_embedding: boolean
  api_key_configured: boolean
  protocol: string
  base_url: string | null
  chat_model: string | null
  is_active: boolean
}

export type LLMProvidersResponse = {
  current_provider: string
  providers: LLMProviderInfo[]
  fallback_chain: string[]
}

export type SessionSummary = {
  id: string
  title: string
  created_at: number
  updated_at: number
  turn_count: number
  kb_scope?: string | null
}

export type PersistedTurn = {
  id: string
  order_idx: number
  question: string
  answer?: string | null
  sources: QaSource[]
  trace: QaTraceStage[]
  used_provider?: string | null
  liked: boolean
  error?: string | null
  created_at: number
}

export type SessionDetail = SessionSummary & {
  turns: PersistedTurn[]
}

export type LogsInfo = {
  files: { name: string; size: number; mtime: number }[]
  total_size: number
  oldest_mtime: number | null
  newest_mtime: number | null
}

export type LogLevelInfo = {
  current: string
  available: string[]
}

export function getFeedbackSources(fb: FeedbackItem): QaSource[] {
  return api.feedback.parseSources(fb.sources_json)
}
