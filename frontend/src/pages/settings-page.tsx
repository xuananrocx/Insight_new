import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import {
  Settings,
  Cpu,
  CheckCircle2,
  Loader2,
  Database,
  HardDrive,
  Layers,
  X,
  Palette,
  Terminal,
  Save,
  Library,
  ChevronUp,
  ChevronDown,
  PanelLeft,
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
import { useTheme } from '@/hooks/use-theme'
import { useBackground, BACKGROUND_OPTIONS, BACKGROUND_PREVIEW_CSS } from '@/hooks/use-background'
import { useGlassOpacity } from '@/hooks/use-glass-opacity'
import { useLocalStorage } from '@/hooks/use-local-storage'
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
                {kb.name}
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

// ===== 侧边栏设置 =====

// 与 app-sidebar 的默认顺序保持一致
const DEFAULT_MENU_ORDER = [
  '/',
  '/knowledge',
  '/kbs',
  '/review',
  '/stats',
  '/ai-logs',
  '/settings',
]

// 菜单路径 → 显示名（与 app-sidebar 的 label 一致）
const MENU_LABELS: Record<string, string> = {
  '/': '提问',
  '/knowledge': '文档管理',
  '/kbs': '知识库管理',
  '/review': '审批',
  '/stats': '分析',
  '/ai-logs': 'AI 日志',
  '/logs': '系统日志',
  '/settings': '设置',
}

function SidebarSettingsCard() {
  const [menuOrder, setMenuOrder] = useLocalStorage<string[]>(
    'amd-ui-menu-order',
    DEFAULT_MENU_ORDER,
  )

  const move = (idx: number, dir: 'up' | 'down') => {
    const next = [...menuOrder]
    const target = dir === 'up' ? idx - 1 : idx + 1
    if (target < 0 || target >= next.length) return
    ;[next[idx], next[target]] = [next[target], next[idx]]
    setMenuOrder(next)
  }

  const reset = () => setMenuOrder(DEFAULT_MENU_ORDER)

  return (
    <Card className="mb-4 p-5">
      <div className="mb-3 flex items-center gap-2">
        <PanelLeft className="h-4 w-4 text-muted-foreground" />
        <span className="text-[14px] font-medium">侧边栏</span>
      </div>
      <p className="mb-3 text-[12px] text-muted-foreground">
        调整左侧菜单的显示顺序。改动立即生效，关闭浏览器后仍保留。
      </p>
      <div className="space-y-1">
        {menuOrder.map((path, idx) => {
          const label = MENU_LABELS[path] ?? path
          return (
            <div
              key={path}
              className="flex items-center justify-between rounded-md border bg-background px-3 py-2 text-[13px]"
            >
              <span className="flex items-center gap-2">
                <span className="text-muted-foreground">{idx + 1}.</span>
                {label}
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => move(idx, 'up')}
                  disabled={idx === 0}
                  className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-30"
                  title="上移"
                >
                  <ChevronUp className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => move(idx, 'down')}
                  disabled={idx === menuOrder.length - 1}
                  className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-30"
                  title="下移"
                >
                  <ChevronDown className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-3 flex justify-end">
        <Button variant="outline" size="sm" onClick={reset} className="text-[12px]">
          恢复默认顺序
        </Button>
      </div>
    </Card>
  )
}

export function SettingsPage() {
  const qc = useQueryClient()
  const [confirmDisable, setConfirmDisable] = useState(false)
  const [showLogs, setShowLogs] = useLocalStorage<boolean>('amd-ui-show-logs', false)
  const [showThinking, setShowThinking] = useLocalStorage<boolean>('amd-ui-show-thinking', true)
  const [showCitations, setShowCitations] = useLocalStorage<boolean>('amd-ui-show-citations', false)
  const { theme } = useTheme()
  const { background, set: setBackground } = useBackground()
  const [glassOpacity, setGlassOpacity] = useGlassOpacity()

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
    <div className="mx-auto max-w-4xl px-8 py-8">
      <div className="mb-6">
        <h1 className="flex items-center gap-2 text-[22px] font-semibold tracking-tight">
          <Settings className="h-5 w-5" />
          设置
        </h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          模型配置、Embedding 切换、系统状态
        </p>
      </div>

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

      <SidebarSettingsCard />

      <Card className="mb-4 p-5">
        <div className="mb-3 text-[14px] font-medium">对话显示</div>
        <div className="flex items-center justify-between gap-4 rounded-md border bg-muted/20 p-3">
          <span>
            <span className="block text-[13px] font-medium">显示思考过程</span>
            <span className="mt-0.5 block text-[11px] text-muted-foreground">默认折叠，可手动展开；回答完成后保留。关闭仅隐藏步骤，不影响记录保存。</span>
          </span>
          <button
            type="button" role="switch" aria-label="显示思考过程" aria-checked={showThinking}
            onClick={() => setShowThinking(!showThinking)}
            className={cn('relative h-5 w-9 shrink-0 rounded-full transition-colors', showThinking ? 'bg-primary' : 'bg-muted')}
          >
            <span className={cn('absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all', showThinking ? 'left-[18px]' : 'left-0.5')} />
          </button>
        </div>
        <div className="mt-3 flex items-center justify-between gap-4 rounded-md border bg-muted/20 p-3">
          <span>
            <span className="block text-[13px] font-medium">显示引用</span>
            <span className="mt-0.5 block text-[11px] text-muted-foreground">同时显示答案中的 [1][2] 等引用编号和引用来源，默认关闭。</span>
          </span>
          <button
            type="button" role="switch" aria-label="显示引用" aria-checked={showCitations}
            onClick={() => setShowCitations(!showCitations)}
            className={cn('relative h-5 w-9 shrink-0 rounded-full transition-colors', showCitations ? 'bg-primary' : 'bg-muted')}
          >
            <span className={cn('absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all', showCitations ? 'left-[18px]' : 'left-0.5')} />
          </button>
        </div>
      </Card>

      <Card className="mb-4 p-5">
        <div className="mb-3 flex items-center gap-2">
          <Palette className="h-4 w-4 text-muted-foreground" />
          <span className="text-[14px] font-medium">外观</span>
        </div>

        {theme === 'macos' ? (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <label className="text-[12px] text-muted-foreground">毛玻璃透明度</label>
              <span className="font-mono text-[11px] text-muted-foreground">
                {Math.round(glassOpacity * 100)}%
              </span>
            </div>
            <input
              type="range"
              min={0.3}
              max={1}
              step={0.05}
              value={glassOpacity}
              onChange={(e) => setGlassOpacity(parseFloat(e.target.value))}
              className="h-2 w-full cursor-pointer appearance-none rounded-full bg-muted accent-primary"
            />
            <div className="mt-1.5 flex justify-between text-[10px] text-muted-foreground">
              <span>透明（看到背景）</span>
              <span>不透明（清晰易读）</span>
            </div>
            <div className="mt-3 text-[11px] text-muted-foreground">
              实时生效，立即看到效果。设置自动保存。
            </div>

            {/* 毛玻璃背景渐变 */}
            <div className="mt-5 mb-2 flex items-center justify-between">
              <label className="text-[12px] text-muted-foreground">背景渐变</label>
              <span className="font-mono text-[11px] text-muted-foreground">
                {BACKGROUND_OPTIONS.find((o) => o.key === background)?.label ?? 'Moonlit Mint'}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {BACKGROUND_OPTIONS.map((opt) => (
                <button
                  key={opt.key}
                  onClick={() => setBackground(opt.key)}
                  className={
                    'relative h-16 rounded-md overflow-hidden transition-all ' +
                    (background === opt.key
                      ? 'ring-2 ring-primary ring-offset-2 ring-offset-background'
                      : 'ring-1 ring-border hover:ring-primary/50')
                  }
                  style={{ background: BACKGROUND_PREVIEW_CSS[opt.key] }}
                  title={`${opt.label} · ${opt.description}`}
                >
                  <span
                    className="absolute bottom-1 left-1.5 text-[10px] font-medium text-white"
                    style={{ textShadow: '0 1px 3px rgba(0,0,0,0.7)' }}
                  >
                    {opt.label}
                  </span>
                </button>
              ))}
            </div>
            <div className="mt-1.5 text-[11px] text-muted-foreground">
              切换主题后背景会自动恢复默认（Moonlit Mint）。
            </div>
          </div>
        ) : (
          <div className="rounded-md bg-muted/20 p-3 text-[12px] text-muted-foreground">
            切换到 macOS 毛玻璃主题后，这里会出现透明度调节。
          </div>
        )}
      </Card>

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

      <LLMTimeoutsCard />

      <SystemPromptCard />

      <AiSummaryConfigCard />

      <AiCallLogConfigCard />

      <Card className="mb-4 p-5">
        <div className="mb-4 flex items-center gap-2">
          <Terminal className="h-4 w-4 text-muted-foreground" />
          <span className="text-[14px] font-medium">开发者选项</span>
        </div>

        <div className="flex items-center justify-between rounded-md border bg-muted/20 p-3">
          <div>
            <div className="text-[13px] font-medium">在侧边栏显示「日志」入口</div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">
              关闭后仍可直接访问 <code className="rounded bg-muted/60 px-1 py-0.5 font-mono text-[10px]">/logs</code> 路径
            </div>
          </div>
          <button
            onClick={() => setShowLogs(!showLogs)}
            className={cn(
              'relative h-5 w-9 shrink-0 rounded-full transition-colors',
              showLogs ? 'bg-primary' : 'bg-muted',
            )}
            aria-label={showLogs ? '关闭' : '开启'}
          >
            <span
              className={cn(
                'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all',
                showLogs ? 'left-[18px]' : 'left-0.5',
              )}
            />
          </button>
        </div>

        <Link
          to="/logs"
          className={cn(
            'mt-3 flex items-center justify-between rounded-md border bg-muted/20 p-3 text-[12px] transition-colors',
            showLogs ? 'hover:bg-accent/30' : 'pointer-events-none opacity-50',
          )}
          aria-disabled={!showLogs}
          title={showLogs ? undefined : '请先开启侧边栏日志入口'}
        >
          <div className="flex items-center gap-2">
            <Terminal className="h-3.5 w-3.5 text-muted-foreground" />
            <span>查看应用日志</span>
          </div>
          <span className="text-muted-foreground">→</span>
        </Link>
      </Card>

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
    </div>
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
