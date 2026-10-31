import { useCallback, useSyncExternalStore } from 'react'
import { accountKey } from '@/lib/account-api'
import { readAppearanceValue } from '@/lib/glass-preference'

const changed = 'insight-appearance-preference-change'
function subscribe(notify: () => void) {
  const events = ['storage', changed, 'insight-theme-account-change']
  events.forEach(name => window.addEventListener(name, notify))
  return () => events.forEach(name => window.removeEventListener(name, notify))
}
// Read the current account on every snapshot; never write defaults during render
// or copy the previous account's state into a newly selected account.
export function useAppearancePreference<T extends string | number>(name: string, normalize: (value: unknown) => T) {
  const value = useSyncExternalStore(subscribe,
    () => normalize(readAppearanceValue(localStorage, accountKey(name))),
    () => normalize(null))
  const set = useCallback((next: T) => {
    localStorage.setItem(accountKey(name), JSON.stringify(normalize(next)))
    window.dispatchEvent(new Event(changed))
  }, [name, normalize])
  return [value, set] as const
}
