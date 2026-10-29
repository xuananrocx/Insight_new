import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/lib/model-edit.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const { modelEditMessage } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('display-only changes do not require identity confirmation', () => {
  assert.equal(modelEditMessage({ renames: [], deletions: [] }, false), null)
})
test('team confirmation distinguishes migration from invalidated deletions', () => {
  const message = modelEditMessage({ renames: [{ old_model_id: 'old', model_id: 'new', defaults: 2, sessions: 3 }], deletions: [{ model_id: 'removed', defaults: 1, sessions: 4 }] }, true)
  for (const detail of ['所有关联用户', 'old → new', '同步 2 个默认选择、3 个会话选择', '4 个会话选择将失效', '运行中请求可能中断', '历史回答和任务记录不变']) assert.ok(message.includes(detail))
})
