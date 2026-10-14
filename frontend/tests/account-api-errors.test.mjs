import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/lib/account-api.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const { accountRequest, accountErrorMessage } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('account requests show FastAPI field validation details without echoing credentials', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ detail: [
    { type: 'string_pattern_mismatch', loc: ['body', 'username'], input: 'private-user', ctx: { pattern: 'private-pattern' } },
    { type: 'string_too_long', loc: ['body', 'token'], input: 'private-token', ctx: { max_length: 200 } },
    { type: 'string_too_short', loc: ['body', 'password'], input: 'private-password', ctx: { min_length: 1 } },
  ] }), { status: 422 }))
  await assert.rejects(accountRequest('/auth/setup', 'POST', {}), (error) => {
    assert.match(error.message, /用户名只能包含/)
    assert.match(error.message, /初始化令牌不能超过 200 个字符/)
    assert.match(error.message, /密码至少需要 1 个字符/)
    assert.doesNotMatch(error.message, /private-/)
    return true
  })
})

test('preserves business messages and handles missing or malformed error details', () => {
  assert.equal(accountErrorMessage({ detail: '初始化令牌不正确' }, 403), '初始化令牌不正确')
  for (const value of [null, {}, { detail: [] }, { detail: [null, {}] }]) {
    assert.equal(accountErrorMessage(value, 422), '请求失败 (422)')
  }
  assert.equal(accountErrorMessage({ detail: [{ loc: ['body', 'username'], type: 'missing' }] }, 422), '用户名不能为空')
  assert.equal(accountErrorMessage({ detail: [{ loc: ['body', 'password'], type: 'value_error', msg: 'private-password', ctx: { error: 'private-token' } }] }, 422), '密码格式不正确，请检查后重试')
})
