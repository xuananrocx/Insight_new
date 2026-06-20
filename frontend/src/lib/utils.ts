import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * 格式化字节大小（KB/MB/GB）。所有页面共用，避免 inline 实现。
 *
 * - < 1 KB → "512 B"
 * - < 1 MB → "12.3 KB"
 * - < 1 GB → "45.67 MB"
 * - >= 1 GB → "1.23 GB"
 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

/**
 * 格式化时间戳。统一 zh-CN + 24h，避免不同浏览器 locale 差异。
 *
 * @param ts 秒级或毫秒级时间戳，或 ISO 字符串
 * @param mode 'datetime' | 'date' | 'time'
 */
export function formatTimestamp(
  ts: number | string | null | undefined,
  mode: 'datetime' | 'date' | 'time' = 'datetime',
): string {
  if (ts == null) return '-'
  // 数字：判断秒级还是毫秒级（10 位 = 秒，13 位 = 毫秒）
  let ms: number
  if (typeof ts === 'number') {
    ms = ts < 1e12 ? ts * 1000 : ts
  } else {
    const parsed = Date.parse(ts)
    if (Number.isNaN(parsed)) return String(ts)
    ms = parsed
  }
  const d = new Date(ms)
  if (mode === 'date') {
    return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
  }
  if (mode === 'time') {
    return d.toLocaleTimeString('zh-CN', { hour12: false })
  }
  return d.toLocaleString('zh-CN', { hour12: false })
}

/**
 * 截断字符串，超出加省略号。多用于文件名/路径展示。
 */
export function truncateText(s: string | null | undefined, max: number): string {
  if (!s) return ''
  if (s.length <= max) return s
  return s.slice(0, Math.max(0, max - 1)) + '…'
}

/**
 * 相对时间（"刚刚 / N 分钟前 / N 小时前 / N 天前 / YYYY-MM-DD"）。
 * 多用于列表项的时间显示，比绝对时间更易扫读。
 *
 * @param ms 毫秒级时间戳
 */
export function formatRelativeTime(ms: number | null | undefined): string {
  if (!ms || !Number.isFinite(ms)) return '-'
  const diff = Date.now() - ms
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`
  return new Date(ms).toLocaleDateString('zh-CN')
}
