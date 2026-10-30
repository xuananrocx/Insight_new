import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
const source = await readFile(new URL('../src/lib/answer-timing.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const { elapsedAnswer, snapshotTiming } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('server duration replaces skewed timestamps and resumes from elapsed duration', () => {
  for (const skew of [-393000, 393000]) {
    const timing = snapshotTiming(1200, { startedAt: Date.now() + skew })
    assert.equal(elapsedAnswer(timing, timing.timerAnchor), 1200)
    assert.equal(elapsedAnswer(timing, timing.timerAnchor + 2000), 3200)
  }
})
test('new request starts at zero; missing duration preserves running clock', () => {
  const previous = { startedAt: Date.now() - 393000, timerAnchor: performance.now(), timerBase: 0 }
  assert.equal(elapsedAnswer(previous, previous.timerAnchor), 0)
  const next = snapshotTiming(undefined, previous)
  assert.ok(next.elapsedMs >= 0 && next.elapsedMs < 1000)
  assert.equal(snapshotTiming(-20, previous).elapsedMs, 0)
})
