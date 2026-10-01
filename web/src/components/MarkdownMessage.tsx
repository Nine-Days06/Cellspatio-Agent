import { useEffect, useRef, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import { Check, Copy } from 'lucide-react'
import { cn } from '@/lib/utils'

function CodeBlock({ children }: { children?: ReactNode }) {
  const ref = useRef<HTMLPreElement>(null)
  const [copied, setCopied] = useState(false)

  async function copy() {
    // markdown 围栏代码的 textContent 尾部带一个语法换行，复制时剥离
    const text = (ref.current?.textContent ?? '').replace(/\n$/, '')
    try {
      await navigator.clipboard?.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // 剪贴板不可用时静默失败，不影响阅读
    }
  }

  return (
    <div className="group relative my-2">
      <button
        type="button"
        aria-label="复制"
        onClick={copy}
        className="absolute right-1 top-1 rounded-md border border-border bg-card px-1.5 py-0.5 text-xs text-muted-foreground hover:text-foreground"
      >
        {copied ? (
          <Check className="size-3" aria-hidden="true" />
        ) : (
          <Copy className="size-3" aria-hidden="true" />
        )}
        {copied ? '已复制' : '复制'}
      </button>
      <pre
        ref={ref}
        className="overflow-x-auto rounded-lg border border-border bg-muted/40 p-3 text-xs leading-relaxed"
      >
        {children}
      </pre>
    </div>
  )
}

export interface MarkdownMessageProps {
  text: string
  className?: string
}

/** Markdown 渲染：进入视口前降级为逐行纯文本（惰性），进入后才挂 ReactMarkdown + 语法高亮。 */
export function MarkdownMessage({ text, className }: MarkdownMessageProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    if (!('IntersectionObserver' in window)) {
      setVisible(true)
      return
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: '200px 0px' },
    )
    observer.observe(host)
    return () => observer.disconnect()
  }, [])

  return (
    <div ref={hostRef} className={cn('text-sm leading-relaxed break-words', className)}>
      {visible ? (
        <ReactMarkdown
          rehypePlugins={[rehypeHighlight]}
          components={{
            pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
            a: ({ href, children }) => (
              <a href={href} target="_blank" rel="noreferrer" className="text-primary underline">
                {children}
              </a>
            ),
          }}
        >
          {text}
        </ReactMarkdown>
      ) : (
        // 降级为逐行纯文本：每行一个块级元素，语义等价于 pre 换行且可被精确查询
        <div className="whitespace-pre-wrap font-sans">
          {text.split('\n').map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        </div>
      )}
    </div>
  )
}
