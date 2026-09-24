import { Loader2, AlertTriangle } from 'lucide-react'
import { ManagementDialog } from '@/components/management-dialog'
import { Button } from '@/components/ui/button'

export type ConfirmDialogProps = {
  /** 默认 true，调用方可条件渲染（外部 unmount 时彻底销毁） */
  open?: boolean
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
  loading?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * 确认弹窗（基于 Radix Dialog）。
 *
 * 自带 a11y：focus trap、Esc 关闭、aria-modal、scroll lock。
 * 三个调用方（system-prompt-card、settings-page、logs-page）共用此组件。
 */
export function ConfirmDialog({
  open = true,
  title,
  message,
  confirmText = '确认',
  cancelText = '取消',
  danger = false,
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) return null
  return (
    <ManagementDialog title={title} onClose={onCancel} busy={loading} className="max-w-md">
        <div className="mb-2 flex items-center gap-2">
          {danger ? (
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-destructive/10">
              <AlertTriangle className="h-4 w-4 text-destructive" />
            </div>
          ) : null}
          {danger && <span className="text-sm font-medium text-destructive">请确认操作影响</span>}
        </div>
        <p className="mb-4 whitespace-pre-wrap text-sm text-muted-foreground">{message}</p>
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={loading}>
            {cancelText}
          </Button>
          <Button
            size="sm"
            variant={danger ? 'destructive' : 'default'}
            onClick={onConfirm}
            disabled={loading}
          >
            {loading ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> : null}
            {confirmText}
          </Button>
        </div>
    </ManagementDialog>
  )
}
