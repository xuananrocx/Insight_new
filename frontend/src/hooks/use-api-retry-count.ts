import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { accountApi } from '@/lib/account-api'

export function useApiRetryCount() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['api-preferences'], queryFn: accountApi.apiPreferences })
  const mutation = useMutation({
    mutationFn: accountApi.retryPreference,
    onSuccess: value => { qc.setQueryData(['api-preferences'], value) },
    onError: (error: Error) => toast.error(error.message),
  })
  return [query.data?.retry_count ?? 10, (value: number) => mutation.mutate(value)] as const
}
