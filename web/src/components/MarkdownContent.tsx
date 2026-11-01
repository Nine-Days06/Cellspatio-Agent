import { useRef, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import { Check, Copy } from 'lucide-react'
import type { MarkdownMessageProps } from './MarkdownMessage'

function CodeBlock({ children }: { children?: ReactNode }) {
  const ref = useRef<HTMLPreElement>(null)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState(false)

  async function copy() {
    // markdown 围栏代码的 textContent 尾部带一个语法换行，复制时剥离
    const text = (ref.current?.textContent ?? '').replace(/\n$/, '')
    try {
      if (!navigator.clipboard) throw new Error('Clipboard unavailable')
      await navigator.clipboard.writeText(text)
      setCopyError(false)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
      setCopyError(true)
    }
  }

  return (
    <div className="group relative my-2">
      <button
        type="button"
        aria-label="复制"
        onClick={copy}
        className="absolute right-2 top-2 flex min-h-9 items-center gap-1 rounded-md border border-border bg-card px-2 text-xs text-muted-foreground hover:text-foreground max-md:min-h-11"
      >
        {copied ? (
          <Check className="size-3" aria-hidden="true" />
        ) : (
          <Copy className="size-3" aria-hidden="true" />
        )}
        {copied ? '已复制' : '复制'}
      </button>
      {copyError && <p role="status" className="pr-16 text-xs text-muted-foreground">无法复制，请手动选择代码。</p>}
      <pre
        ref={ref}
        className="overflow-x-auto rounded-lg border border-border bg-muted/40 px-4 pb-4 pt-14 text-sm leading-relaxed"
      >
        {children}
      </pre>
    </div>
  )
}

export default function MarkdownContent({ text, restrictExternalContent }: MarkdownMessageProps) {
  return (
    <ReactMarkdown
      rehypePlugins={[rehypeHighlight]}
      components={{
        pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
        a: ({ href, children }) =>
          restrictExternalContent && !/^https?:\/\//i.test(href ?? '') ? (
            <span>{children}</span>
          ) : (
            <a href={href} target="_blank" rel="noreferrer" className="text-primary underline">
              {children}
            </a>
          ),
        // 远程图片会泄露访问者地址，因此受限正文只显示替代文本。
        ...(restrictExternalContent
          ? { img: ({ alt }: { alt?: string }) => <span className="text-muted-foreground">{alt || '[图片]'}</span> }
          : {}),
      }}
    >
      {text}
    </ReactMarkdown>
  )
}
