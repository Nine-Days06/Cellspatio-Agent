import { useEffect, useRef, useState, type ComponentType } from 'react'
import { cn } from '@/lib/utils'

export interface MarkdownMessageProps {
  text: string
  className?: string
  /** 模型生成的正文只允许 http(s) 链接，且不自动加载远程图片 */
  restrictExternalContent?: boolean
}

/** Markdown 渲染：进入视口前降级为逐行纯文本（惰性），进入后才下载 Markdown 解析与高亮模块。 */
export function MarkdownMessage({ text, className, restrictExternalContent }: MarkdownMessageProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  const [Content, setContent] = useState<ComponentType<MarkdownMessageProps> | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)

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

  useEffect(() => {
    if (!visible) return
    let ignore = false
    import('./MarkdownContent').then(({ default: Renderer }) => {
      if (!ignore) setContent(() => Renderer)
    }).catch(() => {
      if (!ignore) setLoadFailed(true)
    })
    return () => { ignore = true }
  }, [visible])

  return (
    <div ref={hostRef} className={cn('text-sm leading-relaxed break-words', className)}>
      {loadFailed && <p role="status" className="mb-2 text-xs text-muted-foreground">格式化组件加载失败，仍可阅读原文。</p>}
      {Content ? (
        <Content text={text} restrictExternalContent={restrictExternalContent} />
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
