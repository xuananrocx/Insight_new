type MarkdownNode = {
  type: string
  value?: string
  children?: MarkdownNode[]
  position?: { start: { offset?: number }; end: { offset?: number } }
}

// CommonMark leaves **中文。**后续 as literal text. Repair only this narrow
// punctuation/CJK boundary in parsed prose; never rewrite the Markdown source.
export function remarkCjkStrong() {
  return (tree: MarkdownNode, file: { value: unknown }) => {
    const source = String(file.value)
    const visit = (node: MarkdownNode) => {
      if (['code', 'inlineCode', 'html', 'image', 'imageReference'].includes(node.type)) return
      if (!node.children) return
      node.children = node.children.flatMap(child => {
        if (child.type !== 'text' || !child.value) {
          visit(child)
          return [child]
        }
        const start = child.position?.start.offset
        const end = child.position?.end.offset
        // Escapes and character references have already been decoded by remark.
        // Do not interpret their literal stars as new formatting delimiters.
        if (start === undefined || end === undefined || source.slice(start, end) !== child.value) return [child]
        const pieces: MarkdownNode[] = []
        let cursor = 0
        const pattern = /(?<!\*)\*\*([^*\n]+[\p{P}\p{S}])\*\*(?=[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}])/gu
        for (const match of child.value.matchAll(pattern)) {
          if (/^\s/u.test(match[1])) continue
          if (match.index! > cursor) pieces.push({ type: 'text', value: child.value.slice(cursor, match.index) })
          pieces.push({ type: 'strong', children: [{ type: 'text', value: match[1] }] })
          cursor = match.index! + match[0].length
        }
        if (!cursor) return [child]
        if (cursor < child.value.length) pieces.push({ type: 'text', value: child.value.slice(cursor) })
        return pieces
      })
    }
    visit(tree)
  }
}
