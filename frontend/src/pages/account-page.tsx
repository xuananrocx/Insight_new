import { ProviderSettings } from '@/components/provider-settings'
import { Link } from 'react-router-dom'
import { SettingsLayout } from '@/components/settings-layout'
import { AppearanceSettingsCard, SidebarSettingsCard } from '@/components/personal-appearance-settings'
import { PersonalPreferencesCard } from '@/components/personal-preferences-card'
import { PersonalDefaultKbCard } from '@/components/personal-default-kb-card'
import { DeepAiOptionsFields } from '@/components/deep-ai-options'
import { OptionSelect } from '@/components/ui/select'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ManagementDialog } from '@/components/management-dialog'
import { PasswordForm, useAuth } from '@/hooks/use-auth'
import { useLocalStorage } from '@/hooks/use-local-storage'
import { useApiRetryCount } from '@/hooks/use-api-retry-count'


export { Toggle } from '@/components/ui/toggle-switch'
import { Toggle } from '@/components/ui/toggle-switch'
import { AccessEditor } from '@/components/access-editor'

export function Grants({ path, isKb = false }: { path: string; isKb?: boolean }) {
  const [open, setOpen] = useState(false)
  return <><Button size="sm" variant="outline" onClick={() => setOpen(true)}>{isKb ? '管理知识库授权' : '使用授权'}</Button>{open && <ManagementDialog title={isKb ? '知识库授权' : 'API 使用授权'} onClose={() => setOpen(false)}><AccessEditor kind={isKb ? 'kb' : 'api'} id={path.split('/').pop()!} /></ManagementDialog>}</>
}

export { ProviderSettings } from '@/components/provider-settings'

export function AccountPage() {
  const { user, logout, can } = useAuth()
  const [chatLayout, setChatLayout] = useLocalStorage<string>('amd-chat-layout', 'classic')
  const [thinking, setThinking] = useLocalStorage('amd-ui-show-thinking', true)
  const [showContext, setShowContext] = useLocalStorage('amd-ui-show-context', false)
  const [citations, setCitations] = useLocalStorage('amd-ui-show-citations', false)
  const [logs, setLogs] = useLocalStorage('amd-ui-show-logs', false)
  const [apiRetryCount, setApiRetryCount] = useApiRetryCount()
  return <SettingsLayout title="个人设置" description={`${user.username} · ${user.roles.filter(r => r.enabled).map(r => r.name).join('、') || '无启用角色'}`} sections={[
    { id: 'appearance', label: '外观与布局', description: '主题、背景与侧边栏', content: <>
      <AppearanceSettingsCard />
      <SidebarSettingsCard />
      {can('logs.view') && <Card className="p-5"><div className="flex items-center justify-between gap-4 text-sm"><span>在侧边栏显示「系统日志」入口</span><Toggle label="显示系统日志入口" checked={logs} onChange={setLogs} /></div><Link to="/logs" aria-disabled={!logs} title={logs ? undefined : '请先开启侧边栏日志入口'} className={`mt-3 block rounded-md border p-3 text-sm ${logs ? 'hover:bg-accent/30' : 'pointer-events-none opacity-50'}`}>查看系统日志 →</Link></Card>}
    </> },
    { id: 'display', label: '会话显示', description: '思考过程、引用与上下文', content: <>
    <Card className="space-y-4 p-5"><h2 className="font-semibold">显示偏好</h2><div className="flex items-center justify-between gap-4 text-sm"><span>对话布局</span><OptionSelect aria-label="对话布局" value={chatLayout === 'bubble' ? 'bubble' : 'classic'} onValueChange={setChatLayout} options={[{ value: 'classic', label: '经典卡片（默认）' }, { value: 'bubble', label: '气泡' }]} /></div><p className="text-xs text-muted-foreground">经典卡片将问题和回答上下排列；气泡将问题靠右突出，回答保持开放排版。仅调整显示，所有会话即时生效，在当前浏览器按账号保存。</p><div className="flex items-center justify-between gap-4 text-sm"><span>显示上下文使用情况（可展开）</span><Toggle label="显示上下文使用情况" checked={showContext} onChange={setShowContext} /></div><div className="flex items-center justify-between gap-4 text-sm"><span>展示思考过程（默认折叠）</span><Toggle label="展示思考过程" checked={thinking} onChange={setThinking} /></div><div className="flex items-center justify-between gap-4 text-sm"><span>显示引用编号和引用来源</span><Toggle label="显示引用编号和引用来源" checked={citations} onChange={setCitations} /></div></Card>
    </> },
    { id: 'kb', label: '知识库', description: '提问与文档页的默认选择', content: <>
    <PersonalDefaultKbCard />
    </> },
    { id: 'providers', label: 'API 与连接', description: 'API 选择、配置与重试', content: <>
    <ProviderSettings />
    <Card className="space-y-3 p-5"><h2 className="font-semibold">API 请求重试</h2>
      <div className="flex items-center justify-between gap-4 text-sm"><span>API 失败重试次数</span><OptionSelect aria-label="API 失败重试次数" value={String(apiRetryCount)} onValueChange={value => setApiRetryCount(Number(value))} options={Array.from({ length: 11 }, (_, value) => ({ value: String(value), label: value === 0 ? '不重试' : `${value} 次${value === 10 ? '（默认）' : ''}` }))} /></div>
      <p className="text-xs text-muted-foreground">保存到当前账号，同一服务跨设备同步，适用于增强AI和深度AI。默认最多重试 10 次，间隔约 1、2、4、8、16、30 秒，之后最多 30 秒。所有尝试和等待都计入时间预算，次数不保证用完。已输出内容后中断会保留部分答案；服务要求等待超过 30 秒的限流或暂不可用错误，会提示稍后重试。</p>
    </Card>
    </> },
    { id: 'ai', label: 'AI 回答', description: '个人偏好与回答策略', content: <>
    <PersonalPreferencesCard />
    <Card className="space-y-3 p-5"><h2 className="font-semibold">AI 回答设置</h2><p className="text-sm text-muted-foreground">AI约束策略适用于增强AI和深度AI。增强AI 一次检索后直接回答；深度AI 主动查阅知识库，需要 API 支持工具调用。下方查阅轮数与总时长设置仅用于深度AI。</p><DeepAiOptionsFields />
    </Card>
    </> },
    { id: 'account', label: '账号与安全', description: '账号信息与密码', content: <>
      <Card className="space-y-2 p-5"><h2 className="font-semibold">账号信息</h2><p className="text-sm">{user.username}</p><p className="text-sm text-muted-foreground">{user.roles.filter(r => r.enabled).map(r => r.name).join('、') || '无启用角色'}</p></Card>
    <Card className="p-5"><h2 className="mb-4 font-semibold">修改密码</h2><div className="max-w-md"><PasswordForm onDone={logout} /></div></Card>
    </> },
  ]} />
}
