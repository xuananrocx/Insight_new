// Anchors are runtime-only: performance.now() must never be persisted across reloads.
export type AnswerTiming = { startedAt: number; elapsedMs?: number; timerAnchor?: number; timerBase?: number }
export function elapsedAnswer(t: AnswerTiming, now = performance.now()): number {
  if (t.timerAnchor != null) return Math.max(0, (t.timerBase ?? 0) + now - t.timerAnchor)
  return Math.max(0, t.elapsedMs ?? Date.now() - t.startedAt)
}
export function snapshotTiming(elapsed: number | undefined, previous: AnswerTiming): AnswerTiming {
  const duration = elapsed != null && Number.isFinite(elapsed) ? Math.max(0, elapsed) : elapsedAnswer(previous)
  return { startedAt: Date.now() - duration, elapsedMs: duration, timerBase: duration, timerAnchor: performance.now() }
}
