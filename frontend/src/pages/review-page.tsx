import { useState } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  CheckCircle,
  Check,
  X,
  Loader2,
  Inbox,
  FileText,
  Clock,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { api, type FeedbackItem, getFeedbackSources } from '@/lib/api'
import { cn } from '@/lib/utils'

export function ReviewPage() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'pending' | 'approved'>('pending')

  const pending = useQuery({
    queryKey: ['feedback', 'pending'],
    queryFn: api.feedback.pending,
    refetchInterval: 5000,
  })

  const approved = useQuery({
    queryKey: ['feedback', 'approved'],
    queryFn: api.feedback.approved,
  })

  const reviewMutation = useMutation({
    mutationFn: (vars: { id: number; decision: 'approved' | 'rejected' }) =>
      api.feedback.review(vars.id, vars.decision),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['feedback'] })
      qc.invalidateQueries({ queryKey: ['knowledge', 'stats'] })
    },
  })

  const list = tab === 'pending' ? pending.data ?? [] : approved.data ?? []
  const isLoading = tab === 'pending' ? pending.isLoading : approved.isLoading

  return (
    <div className="mx-auto max-w-4xl px-8 py-8">
      <div className="mb-6">
        <h1 className="flex items-center gap-2 text-[22px] font-semibold tracking-tight">
          <CheckCircle className="h-5 w-5" />
          审批队列
        </h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          审批通过用户点赞的问答，确认后入库成为新知识
        </p>
      </div>

      <div className="mb-4 flex gap-1 border-b">
        <button
          onClick={() => setTab('pending')}
          className={cn(
            'flex items-center gap-1.5 px-3 py-2 text-[13px] transition-colors',
            tab === 'pending'
              ? 'border-b-2 border-primary font-medium text-foreground'
              : 'text-muted-foreground hover:text-foreground',
          )}
        >
          待审批
          {(pending.data?.length ?? 0) > 0 ? (
            <span className="rounded-full bg-destructive px-1.5 py-0.5 text-[10px] font-medium text-white">
              {pending.data?.length}
            </span>
          ) : null}
        </button>
        <button
          onClick={() => setTab('approved')}
          className={cn(
            'flex items-center gap-1.5 px-3 py-2 text-[13px] transition-colors',
            tab === 'approved'
              ? 'border-b-2 border-primary font-medium text-foreground'
              : 'text-muted-foreground hover:text-foreground',
          )}
        >
          已通过
          {(approved.data?.length ?? 0) > 0 ? (
            <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {approved.data?.length}
            </span>
          ) : null}
        </button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12 text-[12px] text-muted-foreground">
          <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
          加载中...
        </div>
      ) : list.length === 0 ? (
        <Card className="flex flex-col items-center justify-center py-16 text-center">
          <Inbox className="mb-3 h-8 w-8 text-muted-foreground/40" />
          <div className="text-[13px] text-muted-foreground">
            {tab === 'pending' ? '审批队列为空' : '还没有已通过的案例'}
          </div>
          <div className="mt-1 text-[11px] text-muted-foreground">
            在提问页点"回答有用"，会出现在这里
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {list.map((fb) => (
            <FeedbackCard
              key={fb.id}
              fb={fb}
              onReview={(decision) => reviewMutation.mutate({ id: fb.id, decision })}
              loading={reviewMutation.isPending && reviewMutation.variables?.id === fb.id}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function FeedbackCard({
  fb,
  onReview,
  loading,
}: {
  fb: FeedbackItem
  onReview: (decision: 'approved' | 'rejected') => void
  loading: boolean
}) {
  const sources = getFeedbackSources(fb)
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-start gap-2">
        <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded bg-secondary text-[10px] font-medium text-secondary-foreground">
          Q
        </span>
        <div className="flex-1 text-[13px] font-medium leading-relaxed">{fb.question}</div>
        {fb.created_at ? (
          <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
            <Clock className="h-3 w-3" />
            {new Date(fb.created_at).toLocaleString('zh-CN', { hour12: false })}
          </span>
        ) : null}
      </div>

      <div className="flex items-start gap-2">
        <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded bg-primary text-[10px] font-medium text-primary-foreground">
          A
        </span>
        <div className="flex-1">
          <div className="whitespace-pre-wrap text-[13px] leading-relaxed">{fb.answer}</div>

          {sources.length > 0 ? (
            <div className="mt-3 space-y-1.5">
              {sources.slice(0, 3).map((s, i) => {
                const path = (s.rel_path as string) || (s.file_path as string) || '未知'
                const name = path.split('/').pop() ?? path
                return (
                  <div
                    key={i}
                    className="flex items-start gap-2 rounded-md border bg-muted/30 p-2 text-[11px]"
                  >
                    <FileText className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />
                    <div className="flex-1 break-all">
                      <span className="font-medium text-foreground">{name}</span>
                      <div className="mt-1 line-clamp-2 text-muted-foreground">
                        {String(s.content).slice(0, 160)}
                        {String(s.content).length > 160 ? '...' : ''}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : null}

          {fb.status === 'approved' && fb.reviewer ? (
            <div className="mt-3 inline-flex items-center gap-1 rounded-md bg-success/10 px-2 py-1 text-[11px] text-success">
              <Check className="h-3 w-3" />
              已通过 · 审批人 {fb.reviewer}
            </div>
          ) : null}
        </div>
      </div>

      {fb.status !== 'approved' ? (
        <div className="mt-3 flex justify-end gap-2 border-t pt-3">
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1.5 text-[12px]"
            onClick={() => onReview('rejected')}
            disabled={loading}
          >
            <X className="h-3.5 w-3.5" />
            拒绝
          </Button>
          <Button
            size="sm"
            className="h-7 gap-1.5 text-[12px]"
            onClick={() => onReview('approved')}
            disabled={loading}
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
            通过并入库
          </Button>
        </div>
      ) : null}
    </Card>
  )
}
