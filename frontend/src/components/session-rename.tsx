import { useState } from 'react'
import { Pencil } from 'lucide-react'
import { toast } from 'sonner'
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { useChatSessionsCtx } from '@/hooks/chat-session-context'

export function SessionRename({ id, title }: { id: string; title: string }) {
  const ctx = useChatSessionsCtx()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(title)
  const [busy, setBusy] = useState(false)
  return <>
    <button type="button" title="重命名会话" className="shrink-0 rounded p-1 text-muted-foreground hover:bg-accent" onClick={() => { setName(title); setOpen(true) }}><Pencil className="h-3.5 w-3.5" /></button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent>
      <DialogTitle>重命名会话</DialogTitle><DialogDescription className="mt-2">自定义名称后，AI不会覆盖它。</DialogDescription>
      <form className="mt-4 space-y-4" onSubmit={async e => {
        e.preventDefault(); if (busy || !name.trim()) return; setBusy(true)
        try { await ctx.renameSession(id, name.trim()); setOpen(false) } catch (error) { toast.error(String(error)) } finally { setBusy(false) }
      }}>
        <input autoFocus aria-label="会话名称" maxLength={80} value={name} onChange={e => setName(e.target.value)} className="w-full rounded border bg-background px-3 py-2 text-sm" />
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={() => setOpen(false)}>取消</Button><Button type="submit" disabled={busy || !name.trim()}>保存</Button></div>
      </form>
    </DialogContent></Dialog>
  </>
}
