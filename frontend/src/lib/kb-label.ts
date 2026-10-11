import type { KB } from '@/lib/api'

type KbLabelLike = Pick<KB, 'id' | 'name' | 'scope' | 'owner_username' | 'name_conflict'>

// 名称后的元信息后缀（· 团队/用户名 · 短ID），供需要弱化展示的调用方单独渲染
export function kbMetaSuffix(kb?: KbLabelLike) {
  if (!kb) return ''
  const owner = kb.scope === 'team' ? '团队' : kb.owner_username
  const parts = [owner, kb.name_conflict ? kb.id.slice(-6) : ''].filter(Boolean)
  return parts.length ? ` · ${parts.join(' · ')}` : ''
}

export function kbLabel(kb?: KbLabelLike) {
  if (!kb) return ''
  return `${kb.name}${kbMetaSuffix(kb)}`
}
