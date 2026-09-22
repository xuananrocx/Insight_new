import { accountKey } from '@/lib/account-api'
import { useLocalStorage } from './use-local-storage'

export function useApiRetryCount() {
  let initial = 10
  try {
    const old = JSON.parse(localStorage.getItem(accountKey('amd-ai-api-retry-count')) ?? 'null')
    // Upgrade the previous default while retaining disabled/custom retry limits.
    if (Number.isInteger(old) && old >= 0 && old <= 10 && old !== 5) initial = old
  } catch { /* Missing or invalid preferences use the current default. */ }
  return useLocalStorage('amd-ai-api-retry-count-v2', initial)
}
