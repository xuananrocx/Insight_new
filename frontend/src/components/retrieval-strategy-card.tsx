import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Database, FileText, Layers, Network } from 'lucide-react'

import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'

interface StrategyOptionProps {
  value: 'basic' | 'summary' | 'agentic'
  current: 'basic' | 'summary' | 'agentic'
  title: string
  description: string
  disabled?: boolean
  onSelect: () => void
}

function StrategyOption({ value, current, title, description, disabled, onSelect }: StrategyOptionProps) {
  const isActive = current === value
  const Icon = value === 'basic' ? FileText : value === 'summary' ? Layers : Network
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      className={cn(
        'flex flex-col items-start gap-1 rounded-md border p-3 text-left transition-colors',
        isActive ? 'border-primary bg-accent/30' : 'hover:bg-accent/30',
        disabled && 'cursor-not-allowed opacity-50',
      )}
    >
      <div className="flex w-full items-center gap-2">
        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="text-[13px] font-medium">{title}</span>
        {isActive ? (
          <span className="ml-auto rounded bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
            当前
          </span>
        ) : null}
      </div>
      <div className="text-[11px] leading-relaxed text-muted-foreground">{description}</div>
    </button>
  )
}

export function RetrievalStrategyCard() {
  const queryClient = useQueryClient()
  const [selectedKbId, setSelectedKbId] = useState<string>('')

  // 1. 拉 KB 列表
  const kbsQuery = useQuery({
    queryKey: ['kbs'],
    queryFn: () => api.kb.list(),
  })

  // 自动选中第一个 KB（或 default）
  useEffect(() => {
    if (!selectedKbId && kbsQuery.data && kbsQuery.data.length > 0) {
      const def = kbsQuery.data.find((k) => k.is_default) || kbsQuery.data[0]
      setSelectedKbId(def.id)
    }
  }, [kbsQuery.data, selectedKbId])

  // 2. 拉当前 KB 的策略
  const strategyQuery = useQuery({
    queryKey: ['retrieval-strategy', selectedKbId],
    queryFn: () => api.retrieval.get(selectedKbId),
    enabled: !!selectedKbId,
  })

  // 3. 更新策略
  const updateMutation = useMutation({
    mutationFn: ({ kbId, strategy }: { kbId: string; strategy: 'basic' | 'summary' | 'agentic' }) =>
      api.retrieval.update(kbId, strategy),
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['retrieval-strategy', vars.kbId] })
    },
  })

  const currentStrategy = (strategyQuery.data?.strategy as 'basic' | 'summary' | 'agentic') || 'basic'

  return (
    <Card className="mb-4 p-5">
      <div className="mb-3 flex items-center gap-2">
        <Database className="h-4 w-4 text-muted-foreground" />
        <span className="text-[14px] font-medium">检索策略（按知识库）</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          迭代 3
        </span>
      </div>

      {/* KB 选择器 */}
      <div className="mb-3 flex items-center gap-2 text-[12px]">
        <label className="text-muted-foreground">目标知识库：</label>
        <select
          value={selectedKbId}
          onChange={(e) => setSelectedKbId(e.target.value)}
          className="rounded border bg-background px-2 py-1 text-[12px] outline-none focus:border-primary"
        >
          {(kbsQuery.data || []).map((kb) => (
            <option key={kb.id} value={kb.id}>
              {kb.name}
              {kb.is_default ? '（默认）' : ''}
            </option>
          ))}
        </select>
        {strategyQuery.isLoading ? (
          <span className="text-muted-foreground">加载中...</span>
        ) : null}
      </div>

      {/* 策略选择 */}
      <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <StrategyOption
          value="basic"
          current={currentStrategy}
          title="基础检索"
          description="只用原文切片做向量+BM25 混合检索。速度快、token 省。适合明确的事实查询。"
          disabled={updateMutation.isPending}
          onSelect={() => updateMutation.mutate({ kbId: selectedKbId, strategy: 'basic' })}
        />
        <StrategyOption
          value="summary"
          current={currentStrategy}
          title="摘要增强"
          description="额外注入相关文档的 AI 摘要，让模型看到全局背景。适合跨段落、概念性问题。"
          disabled={updateMutation.isPending}
          onSelect={() => updateMutation.mutate({ kbId: selectedKbId, strategy: 'summary' })}
        />
        <StrategyOption
          value="agentic"
          current={currentStrategy}
          title="智能扩展"
          description="基于问题中的概念，跨文档召回关联 chunks + 注入摘要。适合跨文档、系统性问题。需先投喂 AI 摘要。"
          disabled={updateMutation.isPending}
          onSelect={() => updateMutation.mutate({ kbId: selectedKbId, strategy: 'agentic' })}
        />
      </div>

      <div className="rounded-md bg-muted/20 p-3 text-[11px] leading-relaxed text-muted-foreground">
        <div className="mb-1 font-medium text-foreground">说明</div>
        切换策略不需要重新投喂，立即生效。
        {currentStrategy === 'summary' ? (
          <div className="mt-1">
            ✓ 已启用摘要增强。若文档尚未生成 AI 摘要（document_meta.processed_level='raw'），将自动降级为基础检索。
          </div>
        ) : null}
        {currentStrategy === 'agentic' ? (
          <div className="mt-1">
            ✓ 已启用智能扩展。流程：基础检索 → 概念匹配 → 跨文档扩展候选 → rerank → 摘要注入。
          </div>
        ) : null}
      </div>
    </Card>
  )
}
