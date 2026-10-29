import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
const source = await readFile(new URL('../src/lib/theme-preference.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const { normalizeTheme, readThemePreference } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const legacy = 'amd-ui-theme'
function storage(entries = []) {
  const values = new Map(entries)
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
}
test('new users inherit defaults without persisting them as personal themes', () => {
  const s = storage()
  assert.equal(readThemePreference(s, `${legacy}:alice`, legacy), null)
  assert.equal(s.getItem(`${legacy}:alice`), null)
  assert.equal(readThemePreference(s, `${legacy}:bob`, legacy), null)
})
test('legacy theme migrates once and never leaks to a second account', () => {
  const s = storage([[legacy, 'linear']])
  assert.equal(readThemePreference(s, legacy, legacy), null)
  assert.equal(readThemePreference(s, `${legacy}:alice`, legacy), 'linear')
  assert.equal(readThemePreference(s, `${legacy}:bob`, legacy), null)
  assert.equal(readThemePreference(s, `${legacy}:alice`, legacy), 'linear')
})
test('explicit inheritance and personal theme survive reload', () => {
  const s = storage([[legacy, 'linear'], [`${legacy}:alice`, 'system'], [`${legacy}:bob`, 'macos']])
  assert.equal(readThemePreference(s, `${legacy}:alice`, legacy), null)
  assert.equal(readThemePreference(s, `${legacy}:bob`, legacy), 'macos')
  assert.equal(normalizeTheme('stripe-dark'), 'minimal')
  assert.equal(normalizeTheme('invalid'), null)
})
