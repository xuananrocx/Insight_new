import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
const source = await readFile(new URL('../src/lib/clipboard.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const { copyText } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
function environment(clipboard, { succeeds = true, modal = false, triggerOnly = false, blocked = false } = {}) {
  let selected, parent, removed = false, restored = false, copies = 0
  globalThis.HTMLElement = class {}
  const dialog = { appendChild(element) { parent = dialog; element.parentElement = dialog } }
  const body = { appendChild(element) { parent = body; element.parentElement = body } }
  const previous = new HTMLElement()
  previous.closest = () => modal && !triggerOnly ? dialog : null
  previous.focus = () => { restored = true; document.activeElement = previous }
  const trigger = new HTMLElement()
  trigger.closest = () => dialog
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard } })
  globalThis.window = { getSelection: () => null }
  globalThis.document = {
    activeElement: previous, body,
    createElement: () => ({
      style: {},
      focus() {
        // Model a modal FocusScope redirecting focus from elements outside it.
        document.activeElement = blocked || (modal && this.parentElement !== dialog) ? previous : this
      },
      select() { if (document.activeElement === this) selected = this.value },
      remove() { removed = true },
    }),
    execCommand: command => { copies++; assert.equal(command, 'copy'); return succeeds },
  }
  return { trigger, state: () => ({ selected, removed, restored, copies, insideDialog: parent === dialog }) }
}
test('awaits Clipboard API success without fallback', async () => {
  let copied
  const { state } = environment({ writeText: async text => { copied = text } })
  await copyText('echo "中文"\nls')
  assert.equal(copied, 'echo "中文"\nls')
  assert.equal(state().selected, undefined)
})
test('missing or denied Clipboard API uses fallback and preserves code', async () => {
  for (const clipboard of [undefined, { writeText: async () => { throw new Error('denied') } }]) {
    const { state } = environment(clipboard)
    await copyText('  bash\n$PATH')
    assert.deepEqual(state(), { selected: '  bash\n$PATH', removed: true, restored: true, copies: 1, insideDialog: false })
  }
})
test('failed fallback rejects and restores focus', async () => {
  const { state } = environment(undefined, { succeeds: false })
  await assert.rejects(copyText('bash'))
  assert.equal(state().removed, true)
  assert.equal(state().restored, true)
})
test('HTTP fallback copies token and address inside the modal focus scope', async () => {
  for (const value of ['insight_mcp_example-secret', 'http://knowledge.example.com:8001/mcp']) {
    const { state } = environment(undefined, { modal: true })
    await copyText(value)
    assert.deepEqual(state(), { selected: value, removed: true, restored: true, copies: 1, insideDialog: true })
  }
})
test('explicit trigger selects its dialog even when focus was outside it', async () => {
  const { state, trigger } = environment({ writeText: async () => { throw new Error('denied') } }, { modal: true, triggerOnly: true })
  await copyText('exact\n  config', trigger)
  assert.equal(state().selected, 'exact\n  config')
  assert.equal(state().insideDialog, true)
})
test('blocked selection rejects instead of falsely reporting successful copying', async () => {
  const { state } = environment(undefined, { blocked: true })
  await assert.rejects(copyText('private token'), /selection unavailable/)
  assert.equal(state().copies, 0)
  assert.equal(state().removed, true)
  assert.equal(state().restored, true)
})
