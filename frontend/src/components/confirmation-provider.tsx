import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { ConfirmDialog } from '@/components/confirm-dialog'

type Ask = (message: string, title?: string) => Promise<boolean>
const Context = createContext<Ask | null>(null)

export function ConfirmationProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<{ message: string; title: string } | null>(null)
  const pending = useRef<((answer: boolean) => void) | null>(null)
  useEffect(() => () => { pending.current?.(false); pending.current = null }, [])
  const finish = (answer: boolean) => {
    const resolve = pending.current
    pending.current = null
    setRequest(null)
    resolve?.(answer)
  }
  const ask: Ask = (message, title = '确认操作') => {
    if (pending.current) return Promise.resolve(false)
    return new Promise(resolve => {
      pending.current = resolve
      setRequest({ message, title })
    })
  }
  return <Context.Provider value={ask}>
    {children}
    {request && <ConfirmDialog {...request} danger onConfirm={() => finish(true)} onCancel={() => finish(false)} />}
  </Context.Provider>
}

export function useConfirm() {
  const ask = useContext(Context)
  if (!ask) throw new Error('ConfirmationProvider missing')
  return ask
}
