import { useLocalStorage } from '@/hooks/use-local-storage'

export type AiConstraintStrategy = 'evidence' | 'balanced' | 'exploratory'
export type DeepAiOptions = { constraint_strategy?: AiConstraintStrategy; max_rounds: number | null; time_limit_enabled: boolean; total_timeout_seconds: number }
export function useDeepAiOptions() {
  const [legacyStrict] = useLocalStorage('amd-ai-strict-knowledge', false)
  const [stored, setValue, reset] = useLocalStorage<DeepAiOptions>('amd-deep-ai-options', {
    max_rounds: 10, time_limit_enabled: false, total_timeout_seconds: 360,
  })
  const valid = ['evidence', 'balanced', 'exploratory'].includes(stored.constraint_strategy ?? '')
  const value: DeepAiOptions & { constraint_strategy: AiConstraintStrategy } = { ...stored, constraint_strategy: valid ? stored.constraint_strategy! : legacyStrict ? 'evidence' : 'balanced' }
  return [value, setValue, reset] as const
}
