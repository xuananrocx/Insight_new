import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
const source = await readFile(new URL('../src/lib/glass-preference.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const { normalizeBackground, normalizeGlass, readAppearanceValue, BACKGROUND_KEYS } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
test('existing backgrounds and opacity survive; invalid preferences use safe defaults', () => {
  for (const key of BACKGROUND_KEYS) assert.equal(normalizeBackground(key), key)
  for (const key of ['bad', 'deep-blue', 'pearl-dusk', 'emerald-vault', 'galaxy-mesh']) assert.equal(normalizeBackground(key), 'moonlit-mint')
  assert.equal(normalizeGlass(.3), .3)
  assert.equal(normalizeGlass(.85), .85)
  assert.equal(normalizeGlass(null), .6)
  assert.equal(normalizeGlass(Infinity), .6)
  assert.equal(normalizeGlass(-1), .3)
  assert.equal(normalizeGlass(3), 1)
})
test('account reads are isolated, tolerate invalid JSON and do not write defaults', () => {
  const values = new Map([['glass:alice', '0.8'], ['glass:bob', '0.35'], ['bad', '{']])
  const storage = { getItem: key => values.get(key) ?? null }
  assert.equal(normalizeGlass(readAppearanceValue(storage, 'glass:alice')), .8)
  assert.equal(normalizeGlass(readAppearanceValue(storage, 'glass:bob')), .35)
  assert.equal(normalizeGlass(readAppearanceValue(storage, 'glass:new')), .6)
  assert.equal(readAppearanceValue(storage, 'bad'), null)
  assert.equal(readAppearanceValue({getItem() {throw Error()}}, 'x'), null)
  assert.equal(values.size, 3)
})
test('first paint restores last rendered background and opacity, not legacy accountless values', async () => {
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8')
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1]
  const values = new Map([['amd-ui-theme-rendered', 'macos'], ['amd-ui-background-rendered', 'pearl-dusk'], ['amd-ui-glass-rendered', '0.8'], ['amd-ui-glass-opacity', '0.3']])
  const classes = [], styles = new Map()
  vm.runInNewContext(script, {localStorage: {getItem:key=>values.get(key)??null}, document:{documentElement:{classList:{add:x=>classes.push(x)},style:{setProperty:(k,v)=>styles.set(k,v)}}}})
  assert.ok(classes.includes('theme-macos'))
  assert.ok(classes.includes('bg-moonlit-mint'))
  assert.equal(styles.get('--glass-alpha'), '0.8')
})

test('Stripe first paint uses minimal moss and later preserves rendered accent', async () => {
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8')
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1]
  for (const [theme, cachedAccent, expected] of [['stripe-light', 'blue', 'moss'], ['minimal', 'stone', 'stone']]) {
    const values = new Map([['amd-ui-theme-rendered', theme], ['amd-ui-accent-rendered', cachedAccent]])
    const classes = [], dataset = {}
    vm.runInNewContext(script, {localStorage:{getItem:key=>values.get(key)??null},document:{documentElement:{dataset,classList:{add:x=>classes.push(x)},style:{setProperty(){}}}}})
    assert.ok(classes.includes('theme-minimal'))
    assert.equal(dataset.accent, expected)
  }
})
