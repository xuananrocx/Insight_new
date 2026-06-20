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
  Network,
  Edit,
  Key,
  Save,
  Eye,
  EyeOff,
  Library,
  ChevronUp,
  ChevronDown,
  PanelLeft,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { RebuildConfirmModal } from '@/components/rebuild-confirm-modal'
import { RebuildProgressDialog } from '@/components/rebuild-progress-dialog'
import { SystemPromptCard } from '@/components/system-prompt-card'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { RetrievalStrategyCard } from '@/components/retrieval-strategy-card'
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
function LLMProviderConfigDialog({
  provider,
  onClose,
  onUpdate,
  isPending,
  onTest,
  isTesting,
}: {
  provider: any
  onClose: () => void
  onUpdate: (config: any) => void
  isPending: boolean
  onTest: (config: any) => void
  isTesting: boolean
}) {
  const [formData, setFormData] = useState({
    base_url: provider?.base_url || '',
    api_key: provider?.api_key || '', // 预填完整 key（默认 password 模式隐藏，点眼睛切换显示）
    model: provider?.chat_model || '',
    enabled: provider?.enabled || false,
  })

  const [showApiKey, setShowApiKey] = useState(false)
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null)

  if (!provider) return null

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onUpdate({
      name: provider.name,
      ...formData,
    })
  }

  const handleTest = () => {
    setTestResult(null)
    onTest({
      name: provider.name,
      ...formData,
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="mx-4 max-w-md rounded-lg border bg-card p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-[16px] font-semibold">配置 Provider</h3>
          <button onClick={onClose} className="rounded p-1 hover:bg-accent">
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1.5 block text-[13px] font-medium">
              Provider 名称
            </label>
            <input
              type="text"
              value={provider.name}
              disabled
              className="w-full rounded-md border bg-muted/50 px-3 py-2 text-[13px] text-muted-foreground"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-[13px] font-medium">
              协议类型
            </label>
            <input
              type="text"
              value={provider.protocol}
              disabled
              className="w-full rounded-md border bg-muted/50 px-3 py-2 text-[13px] text-muted-foreground"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-[13px] font-medium">
              Base URL
            </label>
            <input
              type="text"
              value={formData.base_url}
              onChange={(e) => setFormData({ ...formData, base_url: e.target.value })}
              placeholder="https://api.example.com/v1"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px]"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-[13px] font-medium">
              API Key
            </label>
            <div className="relative">
              <Key className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <input
                type={showApiKey ? 'text' : 'password'}
                value={formData.api_key}
                onChange={(e) => setFormData({ ...formData, api_key: e.target.value })}
                placeholder="sk-..."
                className="w-full rounded-md border border-input bg-background pl-10 pr-10 py-2 text-[13px]"
              />
              <button
                type="button"
                onClick={() => setShowApiKey(!showApiKey)}
                className="absolute right-3 top-2.5 text-muted-foreground hover:text-foreground"
                title={showApiKey ? '隐藏' : '显示'}
              >
                {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {provider.api_key_configured
                ? '已加载已配置的 API Key（可编辑后保存生效）'
                : '请输入 API Key'}
            </p>
          </div>

          <div>
            <label className="mb-1.5 block text-[13px] font-medium">
              模型名称
            </label>
            <input
              type="text"
              value={formData.model}
              onChange={(e) => setFormData({ ...formData, model: e.target.value })}
              placeholder="gpt-4o-mini"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px]"
            />
          </div>

          <div>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={formData.enabled}
                onChange={(e) => setFormData({ ...formData, enabled: e.target.checked })}
                className="h-4 w-4 rounded"
              />
              <span className="text-[13px] font-medium">启用此 Provider</span>
            </label>
          </div>

          {/* 测试结果显示 */}
          {testResult && (
            <div className={`rounded-md p-3 text-[12px] ${
              testResult.success ? 'bg-success/10 text-success' : 'bg-destructive/10 text-destructive'
            }`}>
              {testResult.message}
            </div>
          )}

          <div className="flex justify-between gap-2 pt-2">
            <div className="flex gap-2">
              <Button type="button" variant="outline" size="sm" onClick={onClose}>
                取消
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={handleTest}
                disabled={isTesting || (!formData.api_key && !provider.api_key_configured)}
              >
                {isTesting ? (
                  <>
                    <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                    测试中...
                  </>
                ) : (
                  '测试连接'
                )}
              </Button>
            </div>
            <Button type="submit" size="sm" disabled={isPending}>
              {isPending ? (
                <>
                  <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                  保存中...
                </>
              ) : (
                <>
                  <Save className="mr-2 h-3 w-3" />
                  保存
                </>
              )}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}

// 估算首次切换耗时（用于进度条 ETA）
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
        <select
          value={selected}
          onChange={(e) => {
            const v = e.target.value
            setPendingId(v)
            updateMutation.mutate(v || null)
          }}
          disabled={updateMutation.isPending || kbList.isLoading}
          className="flex-1 rounded-md border bg-background px-3 py-1.5 text-[13px]"
        >
          <option value="">使用内置默认（is_default）</option>
          {(kbList.data ?? []).map((kb) => (
            <option key={kb.id} value={kb.id}>
              {kb.name}
              {kb.is_default ? '（内置）' : ''}
            </option>
          ))}
        </select>
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
  const { theme } = useTheme()
  const { background, set: setBackground } = useBackground()
  const [glassOpacity, setGlassOpacity] = useGlassOpacity()
  const [testingProvider, setTestingProvider] = useState<string | null>(null)
  const [configProvider, setConfigProvider] = useState<string | null>(null)

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
  const llmProviders = useQuery({
    queryKey: ['llm', 'providers'],
    queryFn: api.llm.providers,
  })

  const switchLLMProvider = useMutation({
    mutationFn: api.llm.switch,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['llm', 'providers'] })
      toast.success(`已切换到 ${data.current_provider}`, {
        description: '对正在进行的对话不生效，请重新提问以使用新 provider',
      })
    },
    onError: (error) => {
      toast.error(`切换失败：${error.message}`)
    },
  })

  const testLLMProvider = useMutation({
    mutationFn: api.llm.test,
    onSuccess: (data) => {
      setTestingProvider(null)
      if (data.success) {
        toast.success(`连接测试成功：${data.provider}`)
      } else {
        toast.error(`连接测试失败：${data.error || '未知错误'}`)
      }
    },
    onError: (error) => {
      setTestingProvider(null)
      toast.error(`测试失败：${error.message}`)
    },
  })

  const updateLLMProvider = useMutation({
    mutationFn: (config: { name: string; base_url?: string; api_key?: string; model?: string; enabled?: boolean }) =>
      api.llm.update(config),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['llm', 'providers'] })
      setConfigProvider(null)
      toast.success(`已更新 Provider：${data.name}`)
    },
    onError: (error) => {
      toast.error(`更新失败：${error.message}`)
    },
  })

  const testLLMProviderInDialog = useMutation({
    mutationFn: (config: { name: string; base_url?: string; api_key?: string; model?: string }) =>
      api.llm.testProvider(config),
    onSuccess: (data) => {
      if (data.success) {
        toast.success(`连接测试成功：${data.provider}`)
      } else {
        toast.error(`连接测试失败：${data.error || '未知错误'}`)
      }
    },
    onError: (error) => {
      toast.error(`测试失败：${error.message}`)
    },
  })

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

      <Card className="mb-4 p-5">
        <div className="mb-4 flex items-center gap-2">
          <Network className="h-4 w-4 text-muted-foreground" />
          <span className="text-[14px] font-medium">LLM Provider</span>
          <span className="rounded bg-success/15 px-1.5 py-0.5 text-[10px] text-success">
            多协议支持
          </span>
        </div>

        <div className="mb-3 rounded-md border bg-muted/30 px-3 py-2 text-[12px]">
          当前：<span className="font-mono font-medium">{llmProviders.data?.current_provider ?? '加载中...'}</span>
        </div>

        <div className="space-y-2">
          {llmProviders.data?.providers.map((provider) => (
            <div
              key={provider.name}
              className={cn(
                'flex items-center gap-3 rounded-md border p-3 transition-colors',
                provider.is_active ? 'border-primary bg-accent/30' : 'hover:bg-accent/30',
              )}
            >
              <Network className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-medium">{provider.name}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                    {provider.protocol}
                  </span>
                  {(!provider.api_key_configured || !provider.api_key_configured) && (
                    <span className="text-[10px] text-destructive">缺少 API Key</span>
                  )}
                  {!provider.enabled && provider.api_key_configured && (
                    <span className="text-[10px] text-muted-foreground">未启用</span>
                  )}
                </div>
                <div className="mt-0.5 flex gap-3 text-[11px] text-muted-foreground">
                  <span>模型: {provider.chat_model || '无'}</span>
                  <span>Embedding: {provider.has_embedding ? '支持' : '不支持'}</span>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 gap-1.5 text-[11px]"
                  onClick={() => setConfigProvider(provider.name)}
                >
                  <Edit className="h-3 w-3" />
                  编辑
                </Button>
                {provider.is_active ? (
                  <span className="inline-flex items-center gap-1 text-[11px] font-medium text-primary">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    使用中
                  </span>
                ) : (
                  <>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 gap-1.5 text-[11px]"
                      onClick={() => {
                        if (!provider.api_key_configured) {
                          alert('请先配置 API Key')
                          return
                        }
                        // 先测试连接，测试通过后才允许切换
                        setTestingProvider(provider.name)
                        testLLMProvider.mutate(provider.name, {
                          onSuccess: (data) => {
                            setTestingProvider(null)
                            if (data.success) {
                              // 测试通过，允许切换
                              switchLLMProvider.mutate(provider.name)
                            } else {
                              // 测试失败，显示错误并阻止切换
                              toast.error(`连接测试失败：${data.error || '无法连接到 Provider'}，请先配置正确的 API Key`)
                            }
                          },
                          onError: (error) => {
                            setTestingProvider(null)
                            toast.error(`测试失败：${error.message}，请检查配置`)
                          }
                        })
                      }}
                      disabled={!provider.enabled || !provider.api_key_configured || switchLLMProvider.isPending || testingProvider === provider.name}
                    >
                      {testingProvider === provider.name ? (
                        <>
                          <Loader2 className="h-3 w-3 animate-spin" />
                          测试中...
                        </>
                      ) : switchLLMProvider.isPending ? (
                        <>
                          <Loader2 className="h-3 w-3 animate-spin" />
                          切换中...
                        </>
                      ) : (
                        '切换'
                      )}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 gap-1.5 text-[11px]"
                      onClick={() => {
                        setTestingProvider(provider.name)
                        testLLMProvider.mutate(provider.name)
                      }}
                      disabled={testLLMProvider.isPending || !provider.api_key_configured}
                    >
                      {testingProvider === provider.name && testLLMProvider.isPending ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        '测试'
                      )}
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>

        {llmProviders.data?.providers && llmProviders.data.providers.length > 0 ? (
          <div className="mt-3 text-[11px] text-muted-foreground">
            <div className="flex flex-wrap gap-2">
              <span className="font-medium">支持的协议：</span>
              {Array.from(new Set(llmProviders.data.providers.map(p => p.protocol))).map(protocol => (
                <span key={protocol} className="rounded bg-muted/60 px-2 py-0.5">
                  {protocol === 'openai' ? 'OpenAI 兼容' : protocol === 'anthropic' ? 'Anthropic 原生' : protocol}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </Card>

      {/* LLM Provider 配置对话框 */}
      {configProvider && llmProviders.data?.providers && (
        <LLMProviderConfigDialog
          provider={llmProviders.data.providers.find(p => p.name === configProvider)}
          onClose={() => setConfigProvider(null)}
          onUpdate={(config) => updateLLMProvider.mutate(config)}
          isPending={updateLLMProvider.isPending}
          onTest={(config) => testLLMProviderInDialog.mutate(config)}
          isTesting={testLLMProviderInDialog.isPending}
        />
      )}

      <SystemPromptCard />

      <AiSummaryConfigCard />

      <RetrievalStrategyCard />

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
