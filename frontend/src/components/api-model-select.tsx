import * as SelectPrimitive from '@radix-ui/react-select'
import { Plug } from 'lucide-react'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { ModelSelection, Provider } from '@/lib/account-api'
import { modelChoiceFromValue, modelChoiceValue } from '@/lib/model-selection'

export function ApiModelSelect({ providers, selection, disabled, onChange, compact = false }: {
  providers: Provider[]; selection?: ModelSelection; disabled?: boolean; compact?: boolean
  onChange: (providerId: string, modelId: string) => void
}) {
  const available = providers.filter(p => p.usable)
  const chosen = available.find(p => p.id === selection?.provider_id)
  const valid = selection?.valid && chosen?.models.some(m => m.model_id === selection.model_id)
  const value = valid ? JSON.stringify([selection.provider_id, selection.model_id]) : ''
  return <Select value={value} disabled={disabled} onValueChange={value => {
    const choice = modelChoiceFromValue(value, providers)
    if (choice) onChange(choice.providerId, choice.modelId)
  }}>
    <SelectTrigger className={compact ? 'h-auto w-auto max-w-[min(18rem,65vw)] gap-0.5 px-2 py-1 text-[11px]' : ''} aria-label="选择 API 与模型"
      title={valid ? `${selection.provider_name} · ${selection.model_id}` : selection?.reason || '请选择模型'}>
      <SelectValue placeholder={selection?.provider_id ? '所选模型不可用' : '选择模型'} />
    </SelectTrigger>
    <SelectContent>
      {!valid && selection?.provider_id && <p className="max-w-sm p-2 text-xs text-destructive">{selection.reason || '所选 API 或模型不可用，请重新选择。'}</p>}
      {available.map(p => <SelectGroup key={p.id} className="border-b border-border py-2 first:pt-1 last:border-0 last:pb-1">
        <SelectPrimitive.Label className="flex items-center gap-2 px-2.5 py-1.5 text-xs font-medium text-foreground">
          <Plug aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 break-words">{p.name}</span>
          <span className="ml-auto shrink-0 text-[11px] font-normal text-muted-foreground">{p.scope === 'team' ? '团队' : '个人'}</span>
        </SelectPrimitive.Label>
        {p.models.map(m => <SelectItem className="pl-8 font-normal" key={m.model_id} value={modelChoiceValue(p.id, m.model_id)}>{m.display_name || m.model_id}</SelectItem>)}
      </SelectGroup>)}
      {!providers.some(p => p.usable) && <p className="p-3 text-xs text-muted-foreground">没有可选模型，请添加个人 API 或联系管理员授权。</p>}
    </SelectContent>
  </Select>
}
