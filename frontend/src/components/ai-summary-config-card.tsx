import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { Sparkles, Info } from 'lucide-react'

import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'

export function AiSummaryConfigCard() {
  const queryClient = useQueryClient()
  const [enabled, setEnabled] = useState(false)
  const [minWordCount, setMinWordCount] = useState(100)
  const [loaded, setLoaded] = useState(false)

  const query = useQuery({
    queryKey: ['ai-summary-config'],
    queryFn: () => api.aiSummary.get(),
  })

  useEffect(() => {
    if (query.data && !loaded) {
      setEnabled(query.data.enabled)
      setMinWordCount(query.data.min_word_count)
      setLoaded(true)
    }
  }, [query.data, loaded])

  const updateMutation = useMutation({
    mutationFn: ({ enabled, min_word_count }: { enabled: boolean; min_word_count: number }) =>
      api.aiSummary.update({ enabled, min_word_count }),
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['ai-summary-config'] })
      setEnabled(vars.enabled)
      setMinWordCount(vars.min_word_count)
    },
  })

  const dirty = enabled !== (query.data?.enabled ?? false) ||
    minWordCount !== (query.data?.min_word_count ?? 100)

  return (
    <Card className="mb-4 p-5">
      <div className="mb-3 flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-muted-foreground" />
        <span className="text-[14px] font-medium">AI 摘要 & 概念提取（投喂时）</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          迭代 2
        </span>
      </div>

      <div className="space-y-3">
        {/* 开关 */}
        <div className="flex items-center justify-between rounded-md border bg-muted/20 p-3">
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-medium">投喂时生成 AI 摘要</div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">
              新投喂的文档会自动调用 LLM 生成摘要 + 提取核心概念（每个文档 1 次 LLM 调用）。
              已投喂的旧文档不会自动补跑，需要重投喂或重建。
            </div>
          </div>
          <button
            type="button"
            onClick={() => setEnabled(!enabled)}
            className={cn(
              'relative ml-3 h-5 w-9 shrink-0 rounded-full transition-colors',
              enabled ? 'bg-primary' : 'bg-muted',
            )}
            aria-label={enabled ? '关闭' : '开启'}
          >
            <span
              className={cn(
                'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all',
                enabled ? 'left-[18px]' : 'left-0.5',
              )}
            />
          </button>
        </div>

        {/* 字数阈值 */}
        <div className="rounded-md border bg-muted/20 p-3">
          <div className="mb-2 flex items-center justify-between">
            <label className="text-[12px] text-muted-foreground">最小字数阈值</label>
            <span className="font-mono text-[11px] text-muted-foreground">{minWordCount} 字</span>
          </div>
          <input
            type="range"
            min={20}
            max={500}
            step={10}
            value={minWordCount}
            onChange={(e) => setMinWordCount(Number(e.target.value))}
            className="h-2 w-full cursor-pointer appearance-none rounded-full bg-muted accent-primary"
            disabled={!enabled}
          />
          <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
            <span>20（几乎所有文档）</span>
            <span>500（仅长文档）</span>
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">
            短文档（如配置文件、脚本）跳过 AI 处理，省 token。
          </div>
        </div>

        {/* 保存按钮 */}
        <div className="flex items-center justify-between">
          <div className="flex items-start gap-1.5 text-[10px] text-muted-foreground">
            <Info className="mt-0.5 h-3 w-3 shrink-0" />
            <span>
              启用后，新投喂的文档会调用当前 LLM provider 生成摘要。
              成本：~$0.001/文档（DeepSeek）或 ~$0.025/文档（Claude Sonnet）。
            </span>
          </div>
          <button
            type="button"
            disabled={!dirty || updateMutation.isPending}
            onClick={() => updateMutation.mutate({ enabled, min_word_count: minWordCount })}
            className={cn(
              'rounded border px-3 py-1 text-[11px] font-medium transition-colors',
              dirty
                ? 'border-primary bg-primary text-primary-foreground hover:bg-primary/90'
                : 'border-muted bg-muted text-muted-foreground',
            )}
          >
            {updateMutation.isPending ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </Card>
  )
}
