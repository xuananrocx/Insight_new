import type { KB } from '@/lib/api'

export function kbLabel(kb?: Pick<KB, 'id' | 'name' | 'scope' | 'owner_username' | 'name_conflict'>) {
  if (!kb) return ''
  const owner = kb.scope === 'team' ? '团队' : kb.owner_username
  return `${kb.name}${owner ? ` · ${owner}` : ''}${kb.name_conflict ? ` · ${kb.id.slice(-6)}` : ''}`
}
