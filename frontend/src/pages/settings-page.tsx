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

const SIZE_TO_MB: Record<string, number> = {
  '93MB': 93,
  '400MB': 400,
  '1.2GB': 1200,
}

// LLM Provider 配置对话框组件
function LLMTimeoutsCard() {
  const qc = useQueryClient()
  const timeouts = useQuery({ queryKey: ['llm', 'timeouts'], queryFn: api.llm.timeouts })
  const [form, setForm] = useState<{ test: number; request: number } | null>(null)

  useEffect(() => {
    if (timeouts.data && form === null) {
      setForm({
        test: timeouts.data.test_timeout_seconds,
        request: timeouts.data.request_timeout_seconds,
      })
    }
  }, [timeouts.data, form])

  const saveMutation = useMutation({
    mutationFn: () =>
      api.llm.updateTimeouts({
        test_timeout_seconds: form!.test,
        request_timeout_seconds: form!.request,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['llm', 'timeouts'] })
      toast.success('超时配置已保存')
    },
    onError: (e: Error) => toast.error(`保存失败：${e.message}`),
  })

  if (form === null) return null

  return (
    <Card className="mb-4 p-5">
      <div className="mb-4 flex items-center gap-2">
        <Timer className="h-4 w-4 text-muted-foreground" />
        <span className="text-[14px] font-medium">LLM 超时配置</span>
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
          <label className="mb-1.5 block text-[13px] font-medium">请求超时（秒）</label>
          <input
            type="number"
            min={5}
            max={3600}
            value={form.request}
            onChange={(e) => setForm({ ...form, request: Number(e.target.value) })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px]"
          />
          <p className="mt-1 text-[11px] text-muted-foreground">正式对话请求超时（流式为相邻输出最大间隔），建议 60-300 秒</p>
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


function estimateSeconds(size: string, cached: boolean) {
  if (cached) return 0
  const mb = SIZE_TO_MB[size] ?? 100
  // 下载 + 加载，约 30MB/s
  return Math.ceil(mb / 30) + 2
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
    mutationFn: (kbId: string | null) => api.defaultKb.update(kbId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['settings', 'default_kb'] })
      qc.invalidateQueries({ queryKey: ['kbs'] })
      toast.success(
        data.source === 'config'
          ? `默认知识库已设为：${data.name}`
          : '已恢复为使用内置默认知识库',
      )
      setPendingId('')
    },
    onError: (e: unknown) => {
      toast.error(`设置失败：${e instanceof Error ? e.message : '未知错误'}`)
      setPendingId('')
    },
  })

  const currentValue = defaultKb.data?.kb_id ?? ''
  const selected = pendingId || currentValue

  return (
    <Card className="mb-4 p-5">
      <div className="mb-3 flex items-center gap-2">
        <Library className="h-4 w-4 text-muted-foreground" />
        <span className="text-[14px] font-medium">默认知识库</span>
        {defaultKb.data?.source === 'is_default' ? (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            使用内置默认
          </span>
        ) : (
          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
            已自定义
          </span>
        )}
      </div>
      <p className="mb-3 text-[12px] text-muted-foreground">
        提问页和文档管理页会默认选中此知识库。留空则使用「内置知识库」。
      </p>
      <div className="flex items-center gap-2">
        <Select
          value={selected || 'builtin'}
          onValueChange={(v) => {
            const id = v === 'builtin' ? '' : v
            setPendingId(id)
            updateMutation.mutate(id || null)
          }}
          disabled={updateMutation.isPending || kbList.isLoading}
        >
          <SelectTrigger className="flex-1 py-2 text-[13px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="builtin">使用内置默认（is_default）</SelectItem>
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
      setTimeout(() => {
        setSwitchTarget(null)
        setPrecheckError(null)
      }, 3000)
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
  const etaSeconds = switchingModel ? estimateSeconds(switchingModel.size, isCached) : 0

  const cachedCount = e?.cached_models?.length ?? 0

  return (
    <SettingsLayout title="系统设置" description="管理全局配置。" readOnly={!can('system.edit')} sections={[
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
            etaSeconds={etaSeconds}
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
                      已缓存
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
                  disabled={!!switchTarget || switchMutation.isPending}
                >
                  {switchTarget?.name === m.name ? (
                    <>
                      <Loader2 className="h-3 w-3 animate-spin" />
                      检查中...
                    </>
                  ) : (
                    '切换'
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
      { id: 'documents', label: '文档处理', description: '导入时的摘要与概念提取', content: <AiSummaryConfigCard /> },
      { id: 'ai', label: 'AI 运行配置', description: '系统提示词与请求超时', content: <><LLMTimeoutsCard /><SystemPromptCard /></> },
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
        <div className="fixed bottom-4 right-4 z-50 max-w-sm rounded-md border border-destructive/40 bg-destructive/10 p-3 text-[12px] text-destructive">
          {precheckError}
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
  etaSeconds,
}: {
  size: string
  cached: boolean
  etaSeconds: number
}) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const start = Date.now()
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 500)
    return () => clearInterval(t)
  }, [])

  // 进度：缓存命中→固定 95%；首次→根据 ETA 估算，但不超 90%
  const pct = cached
    ? 95
    : etaSeconds > 0
      ? Math.min(90, Math.round((elapsed / etaSeconds) * 100))
      : 30

  const phase = cached
    ? '正在切换到缓存的模型...'
    : elapsed < 3
      ? '正在加载模型文件...'
      : elapsed < etaSeconds
        ? '正在初始化模型（首次较慢）...'
        : '即将完成...'

  return (
    <div className="mb-4 rounded-md border bg-muted/20 p-3">
      <div className="mb-2 flex items-center justify-between text-[12px]">
        <span className="flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
          {phase}
        </span>
        <span className="text-muted-foreground">
          {cached ? '秒级' : `${elapsed}s / ~${etaSeconds}s`} · {size}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-gradient-to-r from-primary to-primary/60 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      {!cached ? (
        <div className="mt-2 flex items-center gap-1 text-[10px] text-muted-foreground">
          <X className="h-2.5 w-2.5" />
          首次切换需下载，之后切换永远秒级（LRU 缓存）
        </div>
      ) : null}
    </div>
  )
}
