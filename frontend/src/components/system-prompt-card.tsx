import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  FileText,
  Loader2,
  Pencil,
  RotateCcw,
  Save,
  Sparkles,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { MarkdownContent } from '@/components/markdown-content'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { api, type SystemPromptInfo, type SystemPromptTestResult } from '@/lib/api'
import { cn } from '@/lib/utils'

function detectWarnings(text: string): string[] {
  const warns: string[] = []
  if (!/\[\d\]|\[\d+\]|引用|来源/.test(text)) {
    warns.push('未检测到引用编号相关说明，回答可能不带 [1] [2] 编号')
  }
  if (!/未找到|不知道|无法|暂无/.test(text)) {
    warns.push('未检测到"未找到"兜底说明，模型可能编造内容而非承认不知道')
  }
  return warns
}

export function SystemPromptCard() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['settings', 'system_prompt'],
    queryFn: api.prompt.get,
  })

  const [draft, setDraft] = useState('')
  const [isEditing, setIsEditing] = useState(false)
  const [showConfirmSave, setShowConfirmSave] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 600)}px`
  }, [draft])
  const [showConfirmReset, setShowConfirmReset] = useState(false)
  const [showTestPanel, setShowTestPanel] = useState(false)
  const [testQuestion, setTestQuestion] = useState('')
  const [testResult, setTestResult] = useState<SystemPromptTestResult | null>(null)
  const [testError, setTestError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [savedToast, setSavedToast] = useState(false)

  useEffect(() => {
    if (data) setDraft(data.current)
  }, [data])

  const saveMutation = useMutation({
    mutationFn: (text: string) => api.prompt.update(text),
    onSuccess: (info: SystemPromptInfo) => {
      setDraft(info.current)
      setIsEditing(false)
      setShowConfirmSave(false)
      setSaveError(null)
      setSavedToast(true)
      setTimeout(() => setSavedToast(false), 1800)
      qc.invalidateQueries({ queryKey: ['settings', 'system_prompt'] })
    },
    onError: (err: Error) => {
      setSaveError(err.message || '保存失败')
      setShowConfirmSave(false)
    },
  })

  const resetMutation = useMutation({
    mutationFn: () => api.prompt.reset(),
    onSuccess: (info: SystemPromptInfo) => {
      setDraft(info.current)
      setShowConfirmReset(false)
      qc.invalidateQueries({ queryKey: ['settings', 'system_prompt'] })
    },
  })

  const testMutation = useMutation({
    mutationFn: ({ prompt, question }: { prompt: string; question: string }) =>
      api.prompt.test(prompt, question),
    onSuccess: (res: SystemPromptTestResult) => {
      setTestResult(res)
      setTestError(null)
    },
    onError: (err: Error) => {
      setTestError(err.message || '测试失败')
      setTestResult(null)
    },
  })

  if (isLoading || !data) {
    return (
      <Card className="p-5">
        <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          加载 Prompt 配置...
        </div>
      </Card>
    )
  }

  const isDirty = draft.trim() !== data.current.trim()
  const charCount = draft.length
  const overLimit = charCount > data.max_length
  const warnings = isEditing && isDirty ? detectWarnings(draft) : []
  const canSave = isDirty && !overLimit && draft.trim().length > 0

  return (
    <Card className="mb-4 p-5">
      <div className="mb-4 flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-muted-foreground" />
        <span className="text-[14px] font-medium">System Prompt（高级）</span>
        <span className="rounded bg-warning/15 px-1.5 py-0.5 text-[10px] text-warning">
          高级
        </span>
        {data.is_default ? (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            出厂默认
          </span>
        ) : (
          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
            已自定义
          </span>
        )}
      </div>

      <div className="mb-3 flex items-start gap-2 rounded-md border border-warning/30 bg-warning/5 px-3 py-2 text-[11px] text-warning-foreground/90">
        <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-warning" />
        <div className="flex-1">
          错误修改会影响所有新提问的回答质量（引用丢失、模型编造、未找到兜底失效）。
          <button
            onClick={() => setShowConfirmReset(true)}
            disabled={resetMutation.isPending || data.is_default}
            className="ml-2 inline-flex items-center gap-1 rounded bg-warning/15 px-1.5 py-0.5 text-[10px] text-warning underline-offset-2 hover:underline disabled:opacity-50"
          >
            <RotateCcw className="h-2.5 w-2.5" />
            恢复默认
          </button>
        </div>
      </div>

      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value)
          if (saveError) setSaveError(null)
        }}
        readOnly={!isEditing}
        spellCheck={false}
        className={cn(
          'min-h-[200px] w-full resize-none overflow-hidden rounded-md p-3 font-mono text-[12px] leading-relaxed outline-none transition-colors',
          isEditing
            ? 'bg-muted/30 text-foreground/90 focus:bg-muted/40'
            : 'bg-muted/20 text-muted-foreground cursor-default',
          overLimit && isEditing ? 'ring-1 ring-destructive/40' : '',
        )}
        placeholder="输入 system prompt..."
      />

      <div className="mt-2 flex items-center justify-between text-[11px]">
        <span className={cn('tabular-nums', overLimit && isEditing ? 'text-destructive' : 'text-muted-foreground')}>
          {charCount} / {data.max_length} 字符
        </span>
        <div className="flex items-center gap-3">
          {isEditing && isDirty ? (
            <span className="text-primary">未保存</span>
          ) : (
            isEditing && <span className="text-muted-foreground">已保存</span>
          )}
          {isEditing ? (
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={saveMutation.isPending}
                onClick={() => {
                  setDraft(data.current)
                  setSaveError(null)
                  setIsEditing(false)
                }}
                className="text-[12px]"
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={!canSave || saveMutation.isPending}
                onClick={() => setShowConfirmSave(true)}
                className="text-[12px]"
              >
                {saveMutation.isPending ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> : <Save className="mr-1.5 h-3 w-3" />}
                保存
              </Button>
            </div>
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsEditing(true)}
              className="text-[12px]"
            >
              <Pencil className="mr-1.5 h-3 w-3" />
              修改
            </Button>
          )}
        </div>
      </div>

      {warnings.length > 0 ? (
        <div className="mt-2 space-y-1">
          {warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-1.5 text-[11px] text-warning-foreground/80">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-warning" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      ) : null}

      {saveError ? (
        <div className="mt-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] text-destructive">
          {saveError}
        </div>
      ) : null}

      <div className="mt-3">
        <button
          onClick={() => setShowTestPanel((v) => !v)}
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
        >
          {showTestPanel ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          测试当前 Prompt
        </button>
      </div>

      {showTestPanel ? (
        <TestPanel
          question={testQuestion}
          setQuestion={setTestQuestion}
          onRun={() => {
            setTestResult(null)
            setTestError(null)
            testMutation.mutate({ prompt: draft, question: testQuestion })
          }}
          isRunning={testMutation.isPending}
          result={testResult}
          error={testError}
        />
      ) : null}

      {savedToast ? (
        <div className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-md border border-success/30 bg-success/10 px-3 py-2 text-[12px] text-success shadow-lg">
          已保存。建议提一个问题验证效果。
        </div>
      ) : null}

      {showConfirmSave ? (
        <ConfirmDialog
          title="保存 System Prompt"
          message="修改将影响所有新提问的回答风格。确认前请确保引用编号、未找到兜底等关键规则仍然存在。"
          confirmText="确认保存"
          loading={saveMutation.isPending}
          onCancel={() => setShowConfirmSave(false)}
          onConfirm={() => saveMutation.mutate(draft)}
        />
      ) : null}

      {showConfirmReset ? (
        <ConfirmDialog
          title="恢复出厂默认"
          message="当前自定义的 system prompt 将被丢弃，恢复为出厂默认配置。"
          confirmText="确认恢复"
          danger
          loading={resetMutation.isPending}
          onCancel={() => setShowConfirmReset(false)}
          onConfirm={() => resetMutation.mutate()}
        />
      ) : null}
    </Card>
  )
}

function TestPanel({
  question,
  setQuestion,
  onRun,
  isRunning,
  result,
  error,
}: {
  question: string
  setQuestion: (v: string) => void
  onRun: () => void
  isRunning: boolean
  result: SystemPromptTestResult | null
  error: string | null
}) {
  return (
    <div className="mt-3 rounded-md border bg-muted/20 p-3">
      <div className="mb-2 flex items-center gap-2 text-[11px] text-muted-foreground">
        <Sparkles className="h-3 w-3 text-primary/70" />
        <span>试运行：用当前 textarea 里的 prompt（不入库）跑一次问答</span>
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="输入测试问题，例如：ERR-001 怎么排查？"
          className="flex-1 rounded-md bg-muted/30 px-2.5 py-1.5 text-[12px] outline-none focus:bg-muted/40"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && question.trim() && !isRunning) onRun()
          }}
        />
        <Button
          size="sm"
          disabled={!question.trim() || isRunning}
          onClick={onRun}
          className="text-[12px]"
        >
          {isRunning ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> : null}
          运行
        </Button>
      </div>

      {error ? (
        <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] text-destructive">
          {error}
        </div>
      ) : null}

      {result ? (
        <div className="mt-3 space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            回答 · {result.used_provider} · {result.sources.length} chunks
          </div>
          <div className="rounded-md border border-border/40 bg-background/40 p-3 text-[12px] leading-relaxed text-foreground/90">
            <MarkdownContent content={result.answer} />
          </div>
          {result.sources.length > 0 ? (
            <div>
              <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                引用来源 · {result.sources.length} 条
              </div>
              <div className="space-y-1">
                {result.sources.map((s, i) => (
                  <div key={i} className="rounded-md border border-border/40 bg-muted/20 px-2 py-1 text-[11px]">
                    <div className="flex items-center gap-1.5">
                      <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
                      <span className="font-medium text-foreground">{s.source_name}</span>
                      <span className="ml-auto tabular-nums text-muted-foreground">
                        相似度 {s.score.toFixed(3)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

