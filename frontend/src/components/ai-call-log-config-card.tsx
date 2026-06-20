import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Bot, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { api, type AiCallLogConfig } from '@/lib/api'

const SCENE_LABEL: Record<string, string> = {
  qa_chat: '问答',
  summarize: '摘要',
  concept_extract: '概念提取',
  title: '标题',
  test: '测试',
}

export function AiCallLogConfigCard() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['ai-logs', 'config'],
    queryFn: () => api.aiLogs.getConfig(),
  })

  const [draft, setDraft] = useState<AiCallLogConfig | null>(null)
  useEffect(() => {
    if (data) setDraft(data)
  }, [data])

  const updateMutation = useMutation({
    mutationFn: (body: Partial<AiCallLogConfig>) => api.aiLogs.updateConfig(body),
    onSuccess: (r) => {
      setDraft(r.config)
      qc.invalidateQueries({ queryKey: ['ai-logs', 'config'] })
      toast.success('已更新配置')
    },
    onError: (e: Error) => toast.error(`保存失败：${e.message}`),
  })

  const cleanupMutation = useMutation({
    mutationFn: () => api.aiLogs.cleanup(),
    onSuccess: (r) => toast.success(`已清理 ${r.deleted} 条过期日志`),
    onError: (e: Error) => toast.error(`清理失败：${e.message}`),
  })

  if (isLoading || !draft) {
    return (
      <Card className="p-5">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载 AI 调用日志配置...
        </div>
      </Card>
    )
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(data)

  return (
    <Card className="mb-4 p-5">
      <div className="mb-3 flex items-center gap-2">
        <Bot className="h-4 w-4 text-primary" />
        <h3 className="text-[14px] font-semibold">AI 调用日志</h3>
      </div>
      <p className="mb-3 text-[12px] text-muted-foreground">
        记录每次 LLM 调用的完整 prompt、消息、响应（含失败的）。30 天自动清理。
      </p>

      <div className="space-y-3">
        {/* 全局开关 */}
        <label className="flex cursor-pointer items-center gap-2 text-[13px]">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
          />
          <span className="font-medium">启用 AI 调用日志</span>
          <span className="text-muted-foreground">（关闭后所有场景都不记录）</span>
        </label>

        {draft.enabled ? (
          <>
            {/* 字段级开关 */}
            <div className="rounded-md border border-border bg-muted/20 p-3">
              <div className="mb-2 text-[11px] font-medium text-muted-foreground">
                记录字段
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                <FieldCheckbox
                  label="System Prompt"
                  checked={draft.log_system_prompt}
                  onChange={(v) => setDraft({ ...draft, log_system_prompt: v })}
                />
                <FieldCheckbox
                  label="Messages"
                  checked={draft.log_messages}
                  onChange={(v) => setDraft({ ...draft, log_messages: v })}
                />
                <FieldCheckbox
                  label="Response"
                  checked={draft.log_response}
                  onChange={(v) => setDraft({ ...draft, log_response: v })}
                />
              </div>
            </div>

            {/* 场景级开关 */}
            <div className="rounded-md border border-border bg-muted/20 p-3">
              <div className="mb-2 text-[11px] font-medium text-muted-foreground">
                记录场景
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {Object.entries(draft.scenes).map(([scene, enabled]) => (
                  <FieldCheckbox
                    key={scene}
                    label={SCENE_LABEL[scene] || scene}
                    checked={enabled}
                    onChange={(v) =>
                      setDraft({
                        ...draft,
                        scenes: { ...draft.scenes, [scene]: v },
                      })
                    }
                  />
                ))}
              </div>
            </div>

            {/* 保留天数 */}
            <div className="flex items-center gap-3 text-[12px]">
              <label className="text-muted-foreground">保留天数：</label>
              <input
                type="number"
                min={1}
                max={365}
                value={draft.retention_days}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    retention_days: Math.max(1, Math.min(365, Number(e.target.value) || 30)),
                  })
                }
                className="w-20 rounded-md border border-input bg-background px-2 py-1"
              />
              <span className="text-muted-foreground">
                天（超过自动清理）
              </span>
            </div>

            {/* 操作按钮 */}
            <div className="flex items-center gap-2 pt-2">
              <Button
                size="sm"
                disabled={!dirty || updateMutation.isPending}
                onClick={() => updateMutation.mutate(draft)}
              >
                {updateMutation.isPending ? (
                  <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                ) : null}
                保存配置
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => cleanupMutation.mutate()}
                disabled={cleanupMutation.isPending}
              >
                {cleanupMutation.isPending ? (
                  <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                ) : null}
                立即清理过期日志
              </Button>
              {dirty ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => data && setDraft(data)}
                >
                  撤销
                </Button>
              ) : null}
            </div>
          </>
        ) : null}
      </div>
    </Card>
  )
}

function FieldCheckbox({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-[12px]">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{label}</span>
    </label>
  )
}
