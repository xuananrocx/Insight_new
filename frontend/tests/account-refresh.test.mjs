import assert from 'node:assert/strict'
import test from 'node:test'
import { createAccountRefresher } from '../src/lib/account-refresh.ts'
import { apiFetch, clearAccountRequests } from '../src/lib/account-api.ts'

const account = revision => ({ id: 'alice', permission_revision: revision })
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

test('save waits for a fresh revision after an older in-flight poll; next poll does not cancel test', async () => {
  let current = account(1)
  const oldPoll = deferred()
  const pollStarted = deferred()
  let loads = 0
  let cancellations = 0
  const refresh = createAccountRefresher(async () => {
    loads++
    if (loads === 1) { pollStarted.resolve(); return oldPoll.promise }
    return account(2)
  }, () => current, async next => { cancellations++; current = next })
  const polling = refresh()
  await pollStarted.promise
  // Saving updated the server revision while the older poll still held revision 1.
  const afterSave = refresh()
  oldPoll.resolve(account(1))
  await Promise.all([polling, afterSave])
  assert.equal(loads, 2)
  assert.equal(current.permission_revision, 2)
  assert.equal(cancellations, 1)
  // Test starts only after the barrier. Its subsequent timer poll is harmless.
  await refresh()
  assert.equal(cancellations, 1)
})

test('refresh waits for cache cancellation to finish', async () => {
  let current = account(1)
  const cleanup = deferred()
  const started = deferred()
  let finished = false
  const refresh = createAccountRefresher(async () => account(2), () => current, async next => {
    current = next
    started.resolve()
    await cleanup.promise
  })
  const waiting = refresh().then(() => { finished = true })
  await started.promise
  assert.equal(finished, false)
  cleanup.resolve()
  await waiting
  assert.equal(finished, true)
})

test('a failed refresh is propagated and does not poison later refreshes', async () => {
  let calls = 0
  let current = account(1)
  const refresh = createAccountRefresher(async () => {
    if (++calls === 1) throw new Error('offline')
    return account(2)
  }, () => current, async next => { current = next })
  await assert.rejects(refresh(), /offline/)
  await refresh()
  assert.equal(current.permission_revision, 2)
})

test('a late permission response cannot restore a logged-out account', async () => {
  let current = account(1)
  const reply = deferred()
  const started = deferred()
  let applied = false
  const refresh = createAccountRefresher(async () => { started.resolve(); return reply.promise },
    () => current, async () => { applied = true })
  const pending = refresh()
  await started.promise
  current = null
  reply.resolve(account(2))
  await assert.rejects(pending, /登录状态已变化/)
  assert.equal(applied, false)
})

test('real permission cancellation aborts in-flight fetch with a readable reason; later fetch works', async t => {
  clearAccountRequests()
  let first = true
  t.mock.method(globalThis, 'fetch', async (_url, init) => {
    if (!first) return new Response('{}')
    first = false
    return new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true })
    })
  })
  const pending = apiFetch('/test')
  clearAccountRequests('权限或 API 配置已更新，本次请求已取消，请重新操作')
  await assert.rejects(pending, error => error.name === 'AbortError' && error.message.includes('权限或 API 配置已更新'))
  assert.equal((await apiFetch('/test')).status, 200)
})
