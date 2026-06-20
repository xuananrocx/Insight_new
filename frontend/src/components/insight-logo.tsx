// Insight Logo：点阵字母 I。
// 6x3 网格点阵，中列点亮（实色），两侧点暗（淡色）→ 拼出字母 I。
// 同时暗示"向量空间里的多个维度汇聚成一个标识"。

type Size = 'sm' | 'md' | 'lg'

const SIZE_MAP: Record<Size, { w: number; h: number; r: number }> = {
  sm: { w: 22, h: 22, r: 1.6 },
  md: { w: 32, h: 32, r: 2.0 },
  lg: { w: 48, h: 48, r: 2.8 },
}

const ROWS = [12, 20, 28, 36, 44, 52]
const COLS = [24, 32, 40]

export function InsightLogo({
  size = 'md',
  variant = 'solid',
  className,
}: {
  size?: Size
  /** solid：所有点同色（适合放在纯色背景上，点为白色）
   *  duotone：中列实色 + 两侧淡色（适合放在浅背景上，点为品牌色）
   */
  variant?: 'solid' | 'duotone'
  className?: string
}) {
  const { w, h, r } = SIZE_MAP[size]
  const main = variant === 'solid' ? '#FFFFFF' : '#4F46E5'
  const dim = variant === 'solid' ? '#FFFFFF' : '#A5B4FC'
  const dimOpacity = variant === 'solid' ? 0.4 : 1

  return (
    <svg
      width={w}
      height={h}
      viewBox="0 0 64 64"
      className={className}
      aria-label="Insight"
    >
      {ROWS.map((y) =>
        COLS.map((x) => {
          const isCenter = x === 32
          return (
            <circle
              key={`${x}-${y}`}
              cx={x}
              cy={y}
              r={r}
              fill={isCenter ? main : dim}
              opacity={isCenter ? 1 : dimOpacity}
            />
          )
        })
      )}
    </svg>
  )
}
