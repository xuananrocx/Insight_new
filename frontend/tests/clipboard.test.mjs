import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
const source = await readFile(new URL('../src/lib/clipboard.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const { copyText } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
function environment(clipboard, succeeds = true) {
  let selected, removed = false, restored = false
  globalThis.HTMLElement = class {}
  const previous = new HTMLElement()
  previous.focus = () => { restored = true }
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard } })
  globalThis.window = { getSelection: () => null }
  globalThis.document = {
    activeElement: previous, body: { appendChild() {} },
    createElement: () => ({ style: {}, focus() {}, select() { selected = this.value }, remove() { removed = true } }),
    execCommand: command => { assert.equal(command, 'copy'); return succeeds },
  }
  return () => ({ selected, removed, restored })
}
test('awaits Clipboard API success without fallback', async () => {
  let copied
  const state = environment({ writeText: async text => { copied = text } })
  await copyText('echo "中文"\nls')
  assert.equal(copied, 'echo "中文"\nls')
  assert.equal(state().selected, undefined)
})
test('missing or denied Clipboard API uses fallback and preserves code', async () => {
  for (const clipboard of [undefined, { writeText: async () => { throw new Error('denied') } }]) {
    const state = environment(clipboard)
    await copyText('  bash\n$PATH')
    assert.deepEqual(state(), { selected: '  bash\n$PATH', removed: true, restored: true })
  }
})
test('failed fallback rejects and restores focus', async () => {
  const state = environment(undefined, false)
  await assert.rejects(copyText('bash'))
  assert.equal(state().removed, true)
  assert.equal(state().restored, true)
})
