import { Settings2 } from 'lucide-react'

type Props = {
  value: number
  onChange: (v: number) => void
  className?: string
}

export function TopKSelect({ value, onChange, className }: Props) {
  return (
    <div
      className={
        'inline-flex items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-[11px] ' +
        (className ?? '')
      }
      title="答案深度：从知识库取 N 段最相关内容喂给 LLM"
    >
      <Settings2 className="h-3.5 w-3.5 text-muted-foreground" />
      <span className="text-muted-foreground">答案深度</span>
      <select
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="cursor-pointer bg-transparent pr-1 text-[11px] font-medium text-foreground outline-none"
      >
        {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </select>
    </div>
  )
}
