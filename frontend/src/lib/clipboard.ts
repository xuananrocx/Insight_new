/** Support HTTP deployments and embedded browsers without Clipboard API access. */
export async function copyText(text: string, trigger?: HTMLElement): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return
    }
  } catch { /* Try the selection-based path when browser permission is denied. */ }
  const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const selection = window.getSelection()
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange()) : []
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.readOnly = true
  textarea.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none;'
  // Keep the temporary selection inside the active modal's focus scope.
  const dialogSelector = '[role="dialog"], [role="alertdialog"]'
  const container = trigger?.closest<HTMLElement>(dialogSelector) ?? previous?.closest<HTMLElement>(dialogSelector) ?? document.body
  container.appendChild(textarea)
  try {
    textarea.focus({ preventScroll: true })
    textarea.select()
    if (document.activeElement !== textarea) throw new Error('copy selection unavailable')
    if (!document.execCommand('copy')) throw new Error('copy rejected')
  } finally {
    textarea.remove()
    previous?.focus({ preventScroll: true })
    if (selection) {
      selection.removeAllRanges()
      ranges.forEach(range => selection.addRange(range))
    }
  }
}
