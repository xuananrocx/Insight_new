import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { copyText } from '@/lib/clipboard'
import { Check, Copy } from 'lucide-react'
import { remarkCitations } from '@/lib/remark-citations'
import { remarkCjkStrong } from '@/lib/remark-cjk-strong'

export function MarkdownContent({ content, citationCount = 0, citationPrefix = 'citation', onCitationClick, hideCitations = false, reading = false }: {
  content: string
  reading?: boolean
  citationCount?: number
  citationPrefix?: string
  onCitationClick?: (number: number) => void
  hideCitations?: boolean
}) {
  return (
    <div className={reading ? "md-body chat-reading" : "md-body text-[13px] leading-relaxed"}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkCjkStrong, [remarkCitations, { count: citationCount, prefix: citationPrefix, hidden: hideCitations }]]} components={{
        a: ({ node, ...props }) => {
          const prefix = `#${citationPrefix}-`
          const number = props.href?.startsWith(prefix) ? Number(props.href.slice(prefix.length)) : 0
          if (onCitationClick && Number.isInteger(number) && number > 0 && number <= citationCount) {
            return <a {...props} aria-label={`查看引用 ${number}`} onClick={e => { e.preventDefault(); onCitationClick(number) }} />
          }
          return <a target="_blank" rel="noreferrer" {...props} />
        },
        pre: ({ children }) => <>{children}</>,
        code: ({ className, children, ...props }: any) => {
          const text = Array.isArray(children) ? children.join('') : String(children ?? '')
          const isBlock = /language-/.test(className || '') || text.includes('\n')
          if (!isBlock) {
            return <code {...props}>{children}</code>
          }
          return <CodeBlockWithCopy text={text.replace(/\n$/, '')} className={className} />
        },
      }}>
        {content}
      </ReactMarkdown>
    </div>
  )
}

function CodeBlockWithCopy({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  const [copying, setCopying] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])
  const lang = /language-(\w+)/.exec(className || '')?.[1]
  return (
    <div className="relative overflow-hidden rounded-md bg-muted/40">
      <div className="flex items-center justify-between bg-background/50 px-3 py-1">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{lang || 'code'}</span>
        <button
          type="button"
          disabled={copying}
          aria-label={copied ? '代码已复制' : '复制代码'}
          onClick={async () => {
            setCopying(true)
            clearTimeout(timer.current)
            setCopied(false)
            try {
              await copyText(text)
              setCopied(true)
              timer.current = setTimeout(() => setCopied(false), 2000)
            } catch {
              toast.error('复制失败，请选中代码手动复制，或检查浏览器剪贴板权限')
            } finally {
              setCopying(false)
            }
          }}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 text-[12px] leading-relaxed">
        <code className="font-mono text-foreground/90">{text}</code>
      </pre>
    </div>
  )
}
