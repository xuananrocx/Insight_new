type MarkdownNode = {
  type: string
  value?: string
  url?: string
  children?: MarkdownNode[]
}

// Transform text nodes only: code, existing links and images keep their meaning.
export function remarkCitations({ count, prefix, hidden = false }: { count: number; prefix: string; hidden?: boolean }) {
  return (tree: MarkdownNode) => {
    const visit = (node: MarkdownNode) => {
      if (['code', 'inlineCode', 'link', 'linkReference', 'image', 'imageReference', 'html'].includes(node.type)) return
      if (!node.children) return
      node.children = node.children.flatMap(child => {
        if (child.type !== 'text' || !child.value) {
          visit(child)
          return [child]
        }
        const parts: MarkdownNode[] = []
        let cursor = 0
        for (const match of child.value.matchAll(/\[([1-9]\d*)\]/g)) {
          const number = Number(match[1])
          if (!hidden && number > count) continue
          const start = match.index!
          if (start > cursor) parts.push({ type: 'text', value: child.value.slice(cursor, start) })
          if (!hidden) parts.push({ type: 'link', url: `#${prefix}-${number}`, children: [{ type: 'text', value: match[0] }] })
          cursor = start + match[0].length
        }
        if (cursor === 0) return [child]
        if (cursor < child.value.length) parts.push({ type: 'text', value: child.value.slice(cursor) })
        return parts
      })
    }
    visit(tree)
  }
}
