import type { Account } from './account-api'

// A save must fetch fresh permissions after any older poll has settled.
// Sharing the older poll would allow its pre-save revision to escape the barrier.
export function createAccountRefresher(
  load: () => Promise<Account>,
  current: () => Account | null,
  apply: (account: Account) => Promise<void>,
) {
  let tail: Promise<void> = Promise.resolve()
  return () => {
    const next = tail.then(async () => {
      const before = current()
      if (!before) throw new Error('登录状态已变化，请刷新页面后重试')
      const account = await load()
      const active = current()
      if (!active || active.id !== before.id || account.id !== active.id) {
        throw new Error('登录状态已变化，请刷新页面后重试')
      }
      if (account.permission_revision !== active.permission_revision) await apply(account)
    })
    tail = next.catch(() => {})
    return next
  }
}
