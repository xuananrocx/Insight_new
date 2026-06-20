import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useState } from 'react'
import { Check, Copy } from 'lucide-react'

export function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="md-body text-[13px] leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        a: ({ node, ...props }) => <a target="_blank" rel="noreferrer" {...props} />,
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
  const lang = /language-(\w+)/.exec(className || '')?.[1]
  return (
    <div className="relative overflow-hidden rounded-md bg-muted/40">
      <div className="flex items-center justify-between bg-background/50 px-3 py-1">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{lang || 'code'}</span>
        <button
          onClick={() => {
            navigator.clipboard.writeText(text)
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
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
