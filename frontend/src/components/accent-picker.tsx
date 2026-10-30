import { ACCENT_COLORS, type AccentKey } from '@/lib/accent-colors'
import { useTheme } from '@/hooks/use-theme'

export function AccentPicker({ value, onChange, disabled = false }: { value: AccentKey; onChange: (key: AccentKey) => void; disabled?: boolean }) {
  return <div role="group" aria-label="灰白主题强调色" className="grid max-w-sm grid-cols-3 gap-2 sm:grid-cols-6">
    {ACCENT_COLORS.map(c => <button key={c.key} type="button" aria-label={c.label} aria-pressed={value === c.key} disabled={disabled} onClick={() => onChange(c.key)} className={`flex flex-col items-center gap-1.5 rounded-md border px-2 py-2 text-[11px] disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${value === c.key ? 'border-input bg-muted' : 'border-transparent hover:bg-muted/60'}`}>
      <span className="flex h-6 w-6 items-center justify-center rounded-full" style={{ background: c.hex, color: c.key === 'terracotta' ? '#fffaf7' : '#fff' }}>{value === c.key ? '✓' : ''}</span>{c.label}
    </button>)}
  </div>
}
export function PersonalAccentPicker() {
  const { accent, setAccent, resetAccent, followsDefaultAccent } = useTheme()
  return <div className="space-y-2 border-t pt-3"><p className="text-xs font-medium">强调色</p><AccentPicker value={accent} onChange={setAccent} /><button type="button" className="text-xs text-muted-foreground underline underline-offset-4" onClick={resetAccent} disabled={followsDefaultAccent}>{followsDefaultAccent ? '正在使用系统默认颜色' : '使用系统默认颜色'}</button></div>
}
