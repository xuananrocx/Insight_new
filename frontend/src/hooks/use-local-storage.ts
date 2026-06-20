import { useCallback, useEffect, useState } from 'react'

// 自定义事件名：用于同 tab 内多个 useLocalStorage 实例同步
const LS_CHANGE_EVENT = 'amd-ui-local-storage-change'

export function useLocalStorage<T>(key: string, initialValue: T) {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === 'undefined') return initialValue
    try {
      const item = window.localStorage.getItem(key)
      return item ? (JSON.parse(item) as T) : initialValue
    } catch {
      return initialValue
    }
  })

  // 写入 localStorage，并 dispatch 自定义事件让其他实例同步
  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(value))
      window.dispatchEvent(
        new CustomEvent(LS_CHANGE_EVENT, { detail: { key } }),
      )
    } catch {
      // ignore
    }
  }, [key, value])

  // 监听变化（同 tab 通过自定义事件，跨 tab 通过 storage 事件）
  useEffect(() => {
    const syncFromStorage = () => {
      try {
        const item = window.localStorage.getItem(key)
        const newValue = item ? (JSON.parse(item) as T) : initialValue
        setValue((prev) =>
          JSON.stringify(prev) === JSON.stringify(newValue) ? prev : newValue,
        )
      } catch {
        // ignore
      }
    }
    const onCustomChange = (e: Event) => {
      const detail = (e as CustomEvent).detail
      if (detail?.key === key) syncFromStorage()
    }
    window.addEventListener('storage', syncFromStorage)
    window.addEventListener(LS_CHANGE_EVENT, onCustomChange)
    return () => {
      window.removeEventListener('storage', syncFromStorage)
      window.removeEventListener(LS_CHANGE_EVENT, onCustomChange)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const reset = useCallback(() => setValue(initialValue), [initialValue])

  return [value, setValue, reset] as const
}

