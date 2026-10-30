import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
const source = (await readFile(new URL('../src/lib/answer-jobs.ts', import.meta.url), 'utf8')).replace("import { apiFetch } from './account-api'", 'const apiFetch = (...args) => globalThis.jobFetch(...args)')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const { followAnswerJob } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const snap = (revision, state, answer) => ({ revision, state, answer, trace: [], sources: [] })

test('lost create response retries same request, then uses GET and replaces snapshots', async () => {
  const methods = [], snapshots = []
  let call = 0
  globalThis.jobFetch = async (_, init) => {
    methods.push(init.method)
    if (++call === 1) throw new TypeError('network lost after server accepted')
    return Response.json(call === 2 ? snap(1, 'running', 'a') : snap(2, 'done', 'ab'))
  }
  let result
  await followAnswerJob({ session_id: 's', turn_id: 't' }, { onSnapshot: s => snapshots.push(s.answer), onDone: s => { result = s.answer } })
  assert.deepEqual(methods, ['POST', 'POST', 'GET'])
  assert.deepEqual(snapshots, ['a', 'ab'])
  assert.equal(result, 'ab')
})

test('refresh resumes through GET only, including interrupted partial answer', async () => {
  globalThis.jobFetch = async (_, init) => {
    assert.equal(init.method, 'GET')
    return Response.json({ ...snap(4, 'interrupted', 'saved'), error: 'restart' })
  }
  let result
  await followAnswerJob({ session_id: 's', turn_id: 't' }, { onError: s => { result = s } }, undefined, true)
  assert.equal(result.partial, 'saved')
  assert.equal(result.message, 'restart')
})

test('permission rejection never retries generation', async () => {
  let calls = 0, error
  globalThis.jobFetch = async () => { calls++; return Response.json({ detail: 'denied' }, { status: 403 }) }
  await followAnswerJob({ session_id: 's', turn_id: 't' }, { onError: e => { error = e.message } }, undefined, true)
  assert.equal(calls, 1)
  assert.equal(error, 'denied')
})

test('detaching a page does not invoke cancellation or error callbacks', async () => {
  const controller = new AbortController()
  globalThis.jobFetch = async () => { controller.abort(); throw new DOMException('abort', 'AbortError') }
  await followAnswerJob({ session_id: 's', turn_id: 't' }, { onCancelled: () => assert.fail(), onError: () => assert.fail() }, controller.signal, true)
})

test('legacy server duration uses HTTP server time, never the browser clock', async () => {
  const controller = new AbortController()
  globalThis.jobFetch = async () => Response.json({ ...snap(0, 'running', ''), started_at: 100000 }, {
    headers: { Date: new Date(102000).toUTCString() },
  })
  let elapsed
  await followAnswerJob({ session_id: 's', turn_id: 'legacy' }, { onSnapshot: s => {
    elapsed = s.elapsed_ms
    controller.abort()
  } }, controller.signal)
  assert.equal(elapsed, 2000)
})
test('finished server duration survives completion callback unchanged', async () => {
  globalThis.jobFetch = async () => Response.json({ ...snap(5, 'done', 'ok'), started_at: 1000, finished_at: 400000, elapsed_ms: 2200 })
  let elapsed
  await followAnswerJob({ session_id: 's', turn_id: 'done' }, { onDone: s => { elapsed = s.elapsed_ms } })
  assert.equal(elapsed, 2200)
})
