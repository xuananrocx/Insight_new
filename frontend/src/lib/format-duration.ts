/** Display milliseconds, seconds, or minutes without changing stored precision. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '-'
  if (ms >= 60000) return `${Number((ms / 60000).toFixed(1))} min`
  if (ms >= 1000) return `${Number((ms / 1000).toFixed(1))} s`
  return `${Math.round(ms)} ms`
}
