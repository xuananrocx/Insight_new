import { Card } from '@/components/ui/card'
import { Toggle } from '@/components/ui/toggle-switch'
import { useLocalStorage } from '@/hooks/use-local-storage'

export function InterfaceSettingsCard() {
  const [autoExpand, setAutoExpand] = useLocalStorage('amd-chat-header-auto-expand', true)
  return <Card className="space-y-4 p-5">
    <div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium">会话顶部</h3><span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">本地偏好</span></div>
    <div className="flex items-start justify-between gap-4">
      <div><p className="text-sm">鼠标悬停自动展开</p><p className="mt-1 text-xs leading-relaxed text-muted-foreground">默认开启。鼠标停留在会话顶部区域时，自动展开会话信息与知识库选择；关闭后可点击箭头展开。</p></div>
      <Toggle label="鼠标悬停自动展开会话顶部" checked={autoExpand} onChange={setAutoExpand} />
    </div>
    <p className="border-t pt-3 text-xs leading-relaxed text-muted-foreground">自动保存，仅影响当前账号在此浏览器中的所有会话，不影响其他用户。顶部图钉仍可固定当前展开状态。</p>
  </Card>
}
