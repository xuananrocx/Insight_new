import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { AlertTriangle, Loader2 } from 'lucide-react'

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: React.ReactNode
  /** 确认按钮文字，默认「确认」 */
  confirmText?: string
  /** 取消按钮文字，默认「取消」 */
  cancelText?: string
  /** 确认按钮风格；destructive 用红色（删除等破坏性操作） */
  confirmVariant?: 'default' | 'destructive'
  onConfirm: () => void | Promise<void>
  /** 确认按钮 loading 状态（onConfirm 是 async 时自动管理） */
  loading?: boolean
  /** 是否显示顶部警告图标（破坏性操作推荐） */
  showWarning?: boolean
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmText = '确认',
  cancelText = '取消',
  confirmVariant = 'default',
  onConfirm,
  loading = false,
  showWarning = false,
}: Props) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onOpenChange(false)}>
      <DialogContent className="max-w-md" aria-describedby={undefined}>
        <div className="flex items-start gap-3">
          {showWarning ? (
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
          ) : null}
          <div className="flex-1">
            <DialogTitle className="text-[15px] font-semibold">{title}</DialogTitle>
            {description ? (
              <DialogDescription className="mt-2 text-[13px] text-muted-foreground">
                {description}
              </DialogDescription>
            ) : null}
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={loading}
          >
            {cancelText}
          </Button>
          <Button
            variant={confirmVariant}
            size="sm"
            onClick={() => onConfirm()}
            disabled={loading}
          >
            {loading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
            {confirmText}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
