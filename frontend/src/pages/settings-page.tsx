import { Toggle } from '@/components/ui/toggle-switch'
import { InterfaceSettingsCard, DefaultThemeSettingsCard } from '@/components/interface-settings-card'
import { SettingsLayout } from '@/components/settings-layout'
import { useAuth } from '@/hooks/use-auth'
import { kbLabel } from '@/lib/kb-label'
import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Cpu,
  CheckCircle2,
  Loader2,
  Database,
  HardDrive,
  Layers,
  X,
  Save,
  Library,
  Timer,
  SlidersHorizontal,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { RebuildConfirmModal } from '@/components/rebuild-confirm-modal'
import { RebuildProgressDialog } from '@/components/rebuild-progress-dialog'
import { SystemPromptCard } from '@/components/system-prompt-card'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { AiSummaryConfigCard } from '@/components/ai-summary-config-card'
import { AiCallLogConfigCard } from '@/components/ai-call-log-config-card'
import { api, type EmbeddingPrecheckResult, type EmbeddingRebuildStatus } from '@/lib/api'
import { cn } from '@/lib/utils'

// LLM Provider 配置对话框组件
function LLMTimeoutsCard() {
  const qc = useQueryClient()
  const timeouts = useQuery({ queryKey: ['llm', 'timeouts'], queryFn: api.llm.timeouts })
  const [draft, setForm] = useState<{ test: number; request: number } | null>(null)
  const form = draft ?? (timeouts.data ? {
    test: timeouts.data.test_timeout_seconds,
    request: timeouts.data.request_timeout_seconds,
  } : null)

  const saveMutation = useMutation({
    mutationFn: () =>
      api.llm.updateTimeouts({
        test_timeout_seconds: form!.test,
        request_timeout_seconds: form!.request,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['llm', 'timeouts'] })
      qc.invalidateQueries({ queryKey: ['ai-summary-config'] })
      qc.invalidateQueries({ queryKey: ['summary-preview'] })
      setForm(null)
      toast.success('超时配置已保存')
    },
    onError: (e: Error) => toast.error(`保存失败：${e.message}`),
  })

  if (form === null) return null

  return (
    <Card className="mb-4 p-5">
      <div className="mb-4 flex items-center gap-2">
        <Timer className="h-4 w-4 text-muted-foreground" />
        <span className="text-[14px] font-medium">通用连接</span>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="mb-1.5 block text-[13px] font-medium">连接测试超时（秒）</label>
          <input
            type="number"
            min={1}
            max={300}
            value={form.test}
            onChange={(e) => setForm({ ...form, test: Number(e.target.value) })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px]"
          />
          <p className="mt-1 text-[11px] text-muted-foreground">「测试」按钮的等待时长，建议 5-15 秒</p>
        </div>
        <div>
          <label className="mb-1.5 block text-[13px] font-medium">网络读取等待（秒）</label>
          <input
            type="number"
            min={10}
            max={3600}
            value={form.request}
            onChange={(e) => setForm({ ...form, request: Number(e.target.value) })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px]"
          />
          <p className="mt-1 text-[11px] text-muted-foreground">网络持续无数据时的等待上限。摘要默认继承；任务总时长另行设置。</p>
        </div>
      </div>
      <div className="mt-3 flex justify-end">
        <Button
          size="sm"
          className="h-8 gap-1.5"
          disabled={saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Save className="h-3.5 w-3.5" />
          )}
          保存
        </Button>
      </div>
    </Card>
  )
}


function WebSearchConfigCard() {
  const qc = useQueryClient()
  const { can } = useAuth()
  const status = useQuery({ queryKey: ['web-search-status'], queryFn: api.webSearch.get })
  const save = useMutation({
    mutationFn: api.webSearch.update,
    onSuccess: data => {
      qc.setQueryData(['web-search-status'], data)
      void qc.invalidateQueries({ queryKey: ['settings', 'ai_generation'] })
      toast.success(data.enabled ? '联网搜索已开启' : '联网搜索已关闭')
    },
    onError: (error: Error) => toast.error(`保存失败，未生效：${error.message}`),
  })
  return <Card className="p-5">
    <div className="flex items-center justify-between gap-4">
      <div><h3 className="text-sm font-medium">联网搜索</h3><p role="status" className="mt-1 text-xs text-muted-foreground">{save.isPending ? '正在保存…' : status.isError ? '状态读取失败，请重试' : !status.data ? '正在读取…' : status.data.enabled ? '已开启 · 切换即时保存' : '已关闭 · 切换即时保存'}</p></div>
      <Toggle label="启用联网搜索" checked={save.isPending ? save.variables : status.data?.enabled ?? false} disabled={!can('system.edit') || !status.data || save.isPending} onChange={enabled => save.mutate(enabled)} />
    </div>
    {status.isError && <Button variant="outline" size="sm" onClick={() => void status.refetch()}>重试</Button>}
    <p className="mt-3 text-xs text-muted-foreground">仅对新发起的深度 AI 提问生效；模型还需配置联网能力，由 AI 自主决定是否搜索。联网能力检测不受此开关影响。</p>
  </Card>
}

function AiGenerationConfigCard() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['settings', 'ai_generation'],
    queryFn: api.aiGeneration.get,
  })
  const [draft, setDraft] = useState<{
    maxTokens: number
    timeoutSeconds: number
    contextChars: number
    toolTimeout: number
    jobTimeout: number
    jobMax: number
  } | null>(null)
  const [confirmLargeTokens, setConfirmLargeTokens] = useState(false)
  // 服务端数据加载前不渲染表单；用户编辑后以 draft 为准
  const form = draft ?? (data
    ? { maxTokens: data.max_tokens, timeoutSeconds: data.timeout_seconds, contextChars: data.context_chars ?? 48000, toolTimeout: data.tool_timeout_seconds ?? 25, jobTimeout: data.job_timeout_seconds ?? 3600, jobMax: data.job_max_concurrent ?? 4 }
    : null)

  const saveMutation = useMutation({
    mutationFn: (values: { maxTokens: number; timeoutSeconds: number; contextChars: number; toolTimeout: number; jobTimeout: number; jobMax: number }) =>
      api.aiGeneration.update({
        max_tokens: values.maxTokens,
        timeout_seconds: values.timeoutSeconds,
        context_chars: values.contextChars,
        tool_timeout_seconds: values.toolTimeout,
        job_timeout_seconds: values.jobTimeout,
        job_max_concurrent: values.jobMax,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings', 'ai_generation'] })
      toast.success('AI 生成配置已保存')
    },
    onError: (e: Error) => toast.error(`保存失败：${e.message}`),
  })

  if (form === null) return null

  const save = () => {
    if (form.maxTokens > 1_000_000) {
      setConfirmLargeTokens(true)
      return
    }
    saveMutation.mutate(form)
  }

  return (
    <>
    <Card className="mb-4 p-5">
      <div className="mb-2 flex items-center gap-2">
        <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
        <span className="text-[14px] font-medium">问答生成</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          深度 AI 与增强 AI 通用
        </span>
      </div>
      <p className="mb-4 text-[12px] text-muted-foreground">
        输出上限是单次模型调用的 token 上限，推理模型的思考过程也计入其中；达到上限的回答会被标记为部分完成，不再静默截断或反复重试。
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1.5 block text-[13px] font-medium">输出上限（tokens）</label>
          <input
            type="number"
            min={256}
            value={form.maxTokens}
            onChange={(e) => setDraft({ ...form, maxTokens: Number(e.target.value) })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px]"
          />
          <p className="mt-1 text-[11px] text-muted-foreground">
            须符合所选模型的输出能力，建议 32768-131072
          </p>
        </div>
        <div>
          <label className="mb-1.5 block text-[13px] font-medium">生成时长（秒）</label>
          <input
            type="number"
            min={30}
            value={form.timeoutSeconds}
            onChange={(e) => setDraft({ ...form, timeoutSeconds: Number(e.target.value) })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px]"
          />
          <p className="mt-1 text-[11px] text-muted-foreground">作答阶段的最长生成时间，建议 300-1800 秒</p>
        </div>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 border-t pt-3 sm:grid-cols-2">
        <div><label className="mb-1.5 block text-[13px] font-medium">后台生成总时限（秒）</label><input type="number" min={30} max={86400} value={form.jobTimeout} onChange={e => setDraft({ ...form, jobTimeout: Number(e.target.value) })} className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px]" /><p className="mt-1 text-[11px] text-muted-foreground">默认 3600 秒，包含查阅与作答。刷新或关闭页面不会停止；服务重启保留部分回答，但不自动重试。新提问生效。</p></div>
        <div><label className="mb-1.5 block text-[13px] font-medium">后台生成并发上限</label><input type="number" min={1} max={64} value={form.jobMax} onChange={e => setDraft({ ...form, jobMax: Number(e.target.value) })} className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px]" /><p className="mt-1 text-[11px] text-muted-foreground">全站同时生成数量，默认 4；同一会话最多 1 个。达到上限时提示稍后重试，不自动排队。</p></div>
      </div>
      <div className="mt-4 border-t pt-3">
        <div className="mb-2 text-[12px] font-medium text-muted-foreground">深度 AI 专项</div>
        <div className="mb-4"><label className="mb-1.5 block text-[13px] font-medium">单次工具查阅超时（秒）</label><input type="number" min={5} max={600} value={form.toolTimeout} onChange={e => setDraft({ ...form, toolTimeout: Number(e.target.value) })} className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px]" /><p className="mt-1 text-[11px] text-muted-foreground">默认 25 秒，可设 5–600 秒。用于检索、目录与原文读取等工具，不是 API 网络超时；开启整轮时限时仍受剩余时间限制。保存后新提问生效。</p></div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-[13px] font-medium">查阅上下文（字符）</label>
            <input
              type="number"
              min={2000}
              max={1000000}
              value={form.contextChars}
              onChange={(e) => setDraft({ ...form, contextChars: Number(e.target.value) })}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px]"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              单次提问可读取的原文总字符预算，建议 20000-80000；范围 2,000–1,000,000，搜索摘录和原文共用此预算。
            </p>
          </div>
        </div>
      </div>
      <div className="mt-3 flex justify-end">
        <Button size="sm" className="h-8 gap-1.5" disabled={saveMutation.isPending} onClick={save}>
          {saveMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Save className="h-3.5 w-3.5" />
          )}
          保存
        </Button>
      </div>
    </Card>
    {confirmLargeTokens ? (
      <ConfirmDialog
        title="输出上限较大"
        message={`输出上限已设为 ${form.maxTokens.toLocaleString()} tokens，可能超出模型实际支持范围，超出后所有 AI 请求都会被拒绝。确认使用这个值吗？`}
        confirmText="仍然保存"
        loading={saveMutation.isPending}
        onCancel={() => setConfirmLargeTokens(false)}
        onConfirm={() => {
          setConfirmLargeTokens(false)
          saveMutation.mutate(form)
        }}
      />
    ) : null}
    </>
  )
}


function DefaultKbCard() {
  const qc = useQueryClient()
  const defaultKb = useQuery({
    queryKey: ['settings', 'default_kb'],
    queryFn: () => api.defaultKb.get(),
    staleTime: 60_000,
  })
  const kbList = useQuery({
    queryKey: ['kbs'],
    queryFn: () => api.kb.list(),
    staleTime: 60_000,
  })
  const [pendingId, setPendingId] = useState<string>('')

  const updateMutation = useMutation({
    mutationFn: (kbId: string | null) => api.defaultKb.setSystem(kbId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['settings', 'default_kb'] })
      qc.invalidateQueries({ queryKey: ['kbs'] })
      toast.success(
        data.system_kb_id
          ? '系统默认知识库已保存'
          : '已清除系统默认，用户将回退到内置默认',
      )
      setPendingId('')
    },
    onError: (e: unknown) => {
      toast.error(`设置失败：${e instanceof Error ? e.message : '未知错误'}`)
      setPendingId('')
    },
  })

  const currentValue = defaultKb.data?.system_kb_id ?? ''
  const selected = pendingId || currentValue
  const configuredGone =
    !!currentValue && !(kbList.data ?? []).some((kb) => kb.id === currentValue)

  return (
    <Card className="mb-4 p-5">
      <div className="mb-3 flex items-center gap-2">
        <Library className="h-4 w-4 text-muted-foreground" />
        <span className="text-[14px] font-medium">系统默认知识库</span>
        {currentValue ? (
          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">已配置</span>
        ) : (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">未配置</span>
        )}
      </div>
      <p className="mb-3 text-[12px] text-muted-foreground">
        作为未设置个人默认知识库的用户的默认选择；用户可在个人设置中选择自己的默认知识库，优先于本配置。留空则使用内置知识库。
      </p>
      <div className="flex items-center gap-2">
        <Select
          value={selected || 'none'}
          onValueChange={(v) => {
            const id = v === 'none' ? '' : v
            setPendingId(id)
            updateMutation.mutate(id || null)
          }}
          disabled={updateMutation.isPending || kbList.isLoading}
        >
          <SelectTrigger className="flex-1 py-2 text-[13px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">不配置（使用内置默认）</SelectItem>
            {(kbList.data ?? []).map((kb) => (
              <SelectItem key={kb.id} value={kb.id}>
                {kbLabel(kb)}
                {kb.is_default ? '（内置）' : ''}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {updateMutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : null}
      </div>
      {configuredGone ? (
        <p className="mt-2 text-[11px] text-warning">
          当前配置的知识库已不存在或不可见，用户会回退到内置默认，建议重新选择。
        </p>
      ) : null}
    </Card>
  )
}

export function SettingsPage() {
  const qc = useQueryClient()
  const [confirmDisable, setConfirmDisable] = useState(false)
  const { can } = useAuth()

  // 切换流程状态：null → 'prechecking' → 'confirm' → 'switching' → 'rebuilding' → null
  const [switchTarget, setSwitchTarget] = useState<{ name: string; label: string } | null>(null)
  const [precheckResult, setPrecheckResult] = useState<EmbeddingPrecheckResult | null>(null)
  const [precheckError, setPrecheckError] = useState<string | null>(null)
  const [rebuildInfo, setRebuildInfo] = useState<{ name: string; label: string; total: number } | null>(null)

  const embedding = useQuery({
    queryKey: ['embedding', 'status'],
    queryFn: api.embedding.status,
  })

  const switchMutation = useMutation({
    mutationFn: api.embedding.switch,
    onError: (error: Error) => {
      qc.invalidateQueries({ queryKey: ['embedding', 'status'] })
      toast.error(`模型切换失败：${error.message}`)
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['embedding', 'status'] })
      qc.invalidateQueries({ queryKey: ['kbs'] })
      setSwitchTarget(null)
      const mismatched = data.dim_mismatched_kbs ?? []
      if (mismatched.length > 0) {
        toast.warning(
          `已切换到 ${data.current_model}（${data.dimensions}维）。` +
          `⚠️ ${mismatched.length} 个 KB 维度不匹配，请去 KB 管理页点「重建」按钮重新投喂：` +
          mismatched.map((k) => k.name).join('、'),
        )
      } else {
        toast.success(`已切换到 ${data.current_model}（${data.dimensions}维）`)
      }
    },
  })

  const handleSwitch = async (modelName: string, modelLabel: string) => {
    setSwitchTarget({ name: modelName, label: modelLabel })
    setPrecheckResult(null)
    setPrecheckError(null)
    try {
      const result = await api.embedding.precheck(modelName)
      if (result.needs_rebuild) {
        setPrecheckResult(result)
      } else {
        // 维度匹配，直接切换
        switchMutation.mutate(modelName)
        setSwitchTarget(null)
      }
    } catch (e) {
      setPrecheckError(e instanceof Error ? e.message : String(e))
      setSwitchTarget(null)
    }
  }

  const handleConfirmRebuild = async () => {
    if (!switchTarget) return
    const { name, label } = switchTarget
    const total = precheckResult?.doc_count ?? 0
    try {
      await api.embedding.rebuildAndSwitch(name)
      setPrecheckResult(null)
      setSwitchTarget(null)
      setRebuildInfo({ name, label, total })
    } catch (e) {
      setPrecheckError(e instanceof Error ? e.message : String(e))
    }
  }

  const handleRebuildComplete = (status: EmbeddingRebuildStatus) => {
    setRebuildInfo(null)
    qc.invalidateQueries({ queryKey: ['embedding', 'status'] })
    if (status.status === 'failed') {
      setPrecheckError(status.error || '重建失败')
      setTimeout(() => setPrecheckError(null), 5000)
    }
  }

  const cacheMutation = useMutation({
    mutationFn: api.embedding.setCacheSize,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['embedding', 'status'] })
    },
  })

  // LLM Providers 查询
  const e = embedding.data
  const switchingTo = switchMutation.variables
  const switchingModel = e?.available_models.find((m) => m.name === switchingTo)
  const isCached = switchingModel?.is_cached ?? false

  const cachedCount = e?.cached_models?.length ?? 0

  return (
    <SettingsLayout title="系统设置" description="管理全局配置与本地界面偏好。" readOnly={!can('system.edit')} sections={[
      { id: 'retrieval', label: '知识库与检索', description: '默认知识库、向量模型与缓存', content: <>
      <Card className="mb-4 p-5">
        <div className="mb-4 flex items-center gap-2">
          <Cpu className="h-4 w-4 text-muted-foreground" />
          <span className="text-[14px] font-medium">Embedding 模型（全局默认）</span>
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            本地
          </span>
        </div>
        <div className="mb-4 rounded-md bg-muted/30 px-3 py-2 text-[12px]">
          当前：<span className="font-mono font-medium">{e?.current_model ?? '加载中...'}</span>
          {e?.dimensions ? (
            <span className="ml-3 text-muted-foreground">维度 {e.dimensions}</span>
          ) : null}
        </div>

        {switchMutation.isPending && switchingModel ? (
          <SwitchProgress
            size={switchingModel.size}
            cached={isCached}
            needsDownload={switchingModel.availability?.state === 'download_required'}
          />
        ) : null}

        <div className="space-y-2">
          {e?.available_models.map((m) => (
            <div
              key={m.name}
              className={cn(
                'flex items-center gap-3 rounded-md border p-3 transition-colors',
                m.is_active ? 'border-primary bg-accent/30' : 'hover:bg-accent/30',
              )}
            >
              <Database className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-medium">{m.label}</span>
                  <span className="font-mono text-[10px] text-muted-foreground">{m.name}</span>
                  {m.is_cached ? (
                    <span className="inline-flex items-center gap-1 rounded bg-success/15 px-1.5 py-0.5 text-[10px] font-medium text-success">
                      <HardDrive className="h-2.5 w-2.5" />
                      已加载到内存
                    </span>
                  ) : null}
                </div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">
                  {m.description}
                </div>
                <div className="mt-1 flex gap-3 text-[10px] text-muted-foreground">
                  <span>大小 {m.size}</span>
                  <span>维度 {m.dimensions}</span>
                </div>
                {m.availability && <p className="mt-1 text-[11px] text-muted-foreground">{m.availability.message}</p>}
              </div>
              {m.is_active ? (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-primary">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  使用中
                </span>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 gap-1.5 text-[11px]"
                  onClick={() => handleSwitch(m.name, m.label)}
                  disabled={!!switchTarget || switchMutation.isPending || m.availability?.can_switch === false}
                >
                  {switchTarget?.name === m.name ? (
                    <>
                      <Loader2 className="h-3 w-3 animate-spin" />
                      检查中...
                    </>
                  ) : (
                    m.availability?.can_switch === false ? '无法切换' : m.availability?.state === 'download_required' ? '下载并切换' : '切换'
                  )}
                </Button>
              )}
            </div>
          ))}
        </div>
      </Card>

      <DefaultKbCard />

      <Card className="mb-4 p-5">
        <div className="mb-3 flex items-center gap-2">
          <Layers className="h-4 w-4 text-muted-foreground" />
          <span className="text-[14px] font-medium">模型缓存策略</span>
          <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
            cache_size = {e?.cache_size ?? 1}
          </span>
        </div>

        <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <CacheOption
            value={1}
            current={e?.cache_size ?? 1}
            title="单模型缓存"
            cost="内存 ~400MB"
            benefit="提问秒响应（推荐）"
            disabled={cacheMutation.isPending}
            onSelect={() => cacheMutation.mutate(1)}
          />
          <CacheOption
            value={3}
            current={e?.cache_size ?? 1}
            title="多模型缓存"
            cost="内存 ~1.2GB"
            benefit="多个模型切换秒响应"
            disabled={cacheMutation.isPending}
            onSelect={() => cacheMutation.mutate(3)}
          />
          <CacheOption
            value={0}
            current={e?.cache_size ?? 1}
            title="禁用缓存"
            cost="内存最低"
            benefit="⚠ 每次提问重新加载 (~15s)"
            disabled={cacheMutation.isPending}
            onSelect={() => setConfirmDisable(true)}
          />
        </div>

        {e?.cached_models && e.cached_models.length > 0 ? (
          <div className="rounded-md border bg-muted/20 p-3">
            <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              已缓存到内存（{(e.cached_models.length)} 个）
            </div>
            <div className="flex flex-wrap gap-1.5">
              {e.cached_models.map((name) => (
                <span
                  key={name}
                  className="inline-flex items-center gap-1 rounded bg-background px-2 py-0.5 font-mono text-[11px]"
                >
                  <HardDrive className="h-2.5 w-2.5 text-success" />
                  {name}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </Card>

      </> },
      { id: 'ai', label: 'AI 运行配置', description: '通用连接、问答生成与摘要任务', content: <><LLMTimeoutsCard /><WebSearchConfigCard /><AiGenerationConfigCard /><AiSummaryConfigCard /><SystemPromptCard /></> },
      { id: 'interface', label: '界面与交互', description: '系统默认外观与当前账号的界面交互偏好。', content: <><DefaultThemeSettingsCard /><InterfaceSettingsCard /></> },
      { id: 'logs', label: '日志与诊断', description: 'AI 调用记录与清理配置', content: <AiCallLogConfigCard /> },
    ]}>
      {confirmDisable ? (
        <ConfirmDialog
          title="禁用缓存？"
          message={
            cachedCount > 0
              ? `将立即释放 ${cachedCount} 个常驻模型（约 ${estimateMemoryMB(e?.cached_models ?? [], e?.available_models ?? [])}MB）。注意：禁用后每次提问都会重新加载模型（约 15 秒），与是否切换 KB 无关。`
              : '注意：禁用后每次提问都会重新加载模型（约 15 秒），与是否切换 KB 无关。当前没有已缓存的模型。'
          }
          confirmText="确认禁用"
          danger
          loading={cacheMutation.isPending}
          onCancel={() => setConfirmDisable(false)}
          onConfirm={() => {
            cacheMutation.mutate(0, {
              onSuccess: () => setConfirmDisable(false),
            })
          }}
        />
      ) : null}

      {switchTarget && precheckResult ? (
        <RebuildConfirmModal
          modelLabel={switchTarget.label}
          precheck={precheckResult}
          onCancel={() => {
            setSwitchTarget(null)
            setPrecheckResult(null)
          }}
          onConfirm={handleConfirmRebuild}
        />
      ) : null}

      {rebuildInfo ? (
        <RebuildProgressDialog
          modelName={rebuildInfo.label}
          total={rebuildInfo.total}
          onComplete={handleRebuildComplete}
        />
      ) : null}

      {precheckError ? (
        <div role="alert" className="fixed bottom-4 right-4 z-50 max-w-sm rounded-md border border-destructive/40 bg-background p-3 text-[12px] text-destructive">
          {precheckError}
          <button className="ml-3 underline" onClick={() => setPrecheckError(null)}>关闭</button>
        </div>
      ) : null}
    </SettingsLayout>
  )
}

function CacheOption({
  value,
  current,
  title,
  cost,
  benefit,
  disabled,
  onSelect,
}: {
  value: number
  current: number
  title: string
  cost: string
  benefit: string
  disabled?: boolean
  onSelect: () => void
}) {
  const active = current === value
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled || active}
      className={cn(
        'w-full rounded-md border p-3 text-left transition-colors',
        active
          ? 'border-primary bg-accent/30'
          : 'bg-muted/20 hover:bg-accent/30 hover:border-primary/40',
        disabled && 'cursor-not-allowed opacity-60',
      )}
    >
      <div className="mb-1 flex items-center gap-2">
        {active ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-primary" />
        ) : (
          <div className="h-3.5 w-3.5 rounded-full border" />
        )}
        <span className="text-[12px] font-medium">{title}</span>
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">{value}</span>
      </div>
      <div className="text-[11px] text-muted-foreground">{cost}</div>
      <div className="text-[11px] text-success">{benefit}</div>
    </button>
  )
}

function estimateMemoryMB(cached: string[], models: { name: string; size: string }[]) {
  let total = 0
  for (const name of cached) {
    const m = models.find((x) => x.name === name)
    if (!m) continue
    if (m.size.endsWith('GB')) total += parseFloat(m.size) * 1024
    else if (m.size.endsWith('MB')) total += parseFloat(m.size)
  }
  return Math.round(total)
}

function SwitchProgress({
  size,
  cached,
  needsDownload,
}: {
  size: string
  cached: boolean
  needsDownload: boolean
}) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const start = Date.now()
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 500)
    return () => clearInterval(t)
  }, [])

  const phase = cached
    ? '正在切换到内存中的模型...'
    : needsDownload ? '正在下载并加载模型，请勿重复切换...' : '正在加载本地模型...'

  return (
    <div className="mb-4 rounded-md border bg-muted/20 p-3">
      <div className="mb-2 flex items-center justify-between text-[12px]">
        <span className="flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
          {phase}
        </span>
        <span className="text-muted-foreground">
          已等待 {elapsed}s · 模型大小 {size}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full w-1/3 animate-pulse rounded-full bg-primary/60"
        />
      </div>
      {!cached ? (
        <div className="mt-2 flex items-center gap-1 text-[10px] text-muted-foreground">
          <X className="h-2.5 w-2.5" />
          {needsDownload ? '完整下载耗时取决于网络和磁盘空间，当前未提供下载百分比。' : '模型文件已在本地，加载耗时取决于模型大小和设备性能。'}
        </div>
      ) : null}
    </div>
  )
}
