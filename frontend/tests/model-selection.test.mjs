import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/lib/model-selection.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const { modelChoiceFromValue, modelChoiceValue } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const providers = [
  { id: 'personal', usable: true, models: [{ model_id: 'same-model' }, { model_id: 'model/with|separator' }] },
  { id: 'team', usable: true, models: [{ model_id: 'same-model' }] },
]

test('empty native-select events and stale options do not change the selection', () => {
  for (const value of ['', 'broken json', '[]', '["team","removed"]']) {
    assert.equal(modelChoiceFromValue(value, providers), null)
  }
})

test('same model names retain the selected API and IDs are not split on punctuation', () => {
  for (const [providerId, modelId] of [['team', 'same-model'], ['personal', 'same-model'], ['personal', 'model/with|separator']]) {
    assert.deepEqual(modelChoiceFromValue(modelChoiceValue(providerId, modelId), providers), { providerId, modelId })
  }
})

test('a disabled or revoked API cannot be selected from a stale option', () => {
  assert.equal(modelChoiceFromValue(modelChoiceValue('team', 'same-model'), providers.map(p => ({ ...p, usable: false }))), null)
})
