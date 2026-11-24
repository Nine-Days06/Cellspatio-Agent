import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ReasoningPanelProps {
  text: string
  running: boolean
  startedAt?: number | null
  /** 已完成思考段的累计毫秒，不含工具等待。 */
  elapsedMs?: number
  truncated?: boolean
  /** 展开状态由调用方持有，组件在消息重挂载后仍能保持用户选择。 */
  expanded: boolean
  onToggle: () => void
}

function previewOf(text: string): string {
  return text.replace(/\s+/g, ' ').trim().slice(-64)
}

/** 统一的思考条：进行中展示动态指示与字数，结束后展示耗时，均可展开回看。 */
export function ReasoningPanel({
  text,
  running,
  startedAt = null,
  elapsedMs = 0,
  truncated = false,
  expanded,
  onToggle,
}: ReasoningPanelProps) {
  const [now, setNow] = useState(() => Date.now())
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!running) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])

  useEffect(() => {
    if (!expanded) return
    const body = bodyRef.current
    if (body) body.scrollTop = body.scrollHeight
  }, [text, expanded])

  if (!text.trim()) return null

  const activeMs = running && startedAt !== null ? Math.max(0, now - startedAt) : 0
  const elapsed = Math.max(1, Math.floor((elapsedMs + activeMs) / 1000))
  const label = running ? '思考中' : `已思考 ${elapsed} 秒`
  const preview = previewOf(text)

  return (
    <section
      className="mb-3 rounded-xl border border-border/70 bg-muted/30 text-sm"
      data-testid="reasoning-panel"
      aria-label="模型思考过程"
    >
      <button
        type="button"
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left text-muted-foreground"
        aria-expanded={expanded}
        aria-label={expanded ? '收起思考过程' : '展开思考过程'}
        onClick={onToggle}
      >
        {running ? (
          <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" aria-hidden="true" />
        ) : (
          <Check className="size-3.5 shrink-0" aria-hidden="true" />
        )}
        <span className="shrink-0 font-medium">{label}</span>
        <span className="shrink-0 text-xs tabular-nums">{text.length} 字</span>
        {truncated && <span className="shrink-0 text-xs text-amber-600">已截断</span>}
        {!expanded && preview && (
          <span className="min-w-0 flex-1 truncate text-xs opacity-70">{preview}</span>
        )}
        {expanded ? (
          <ChevronDown className="ml-auto size-4 shrink-0" aria-hidden="true" />
        ) : (
          <ChevronRight className="ml-auto size-4 shrink-0" aria-hidden="true" />
        )}
      </button>
      {expanded && (
        <div className="border-t border-border/60 px-3 py-2">
          <div
            ref={bodyRef}
            className={cn(
              'max-h-60 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-6 text-muted-foreground',
            )}
          >
            {text}
          </div>
          <p className="mt-2 text-xs text-muted-foreground/80">
            {truncated && '思考内容超过上限，已停止追加。'}
            模型自述，不是证据，可能不准确。
          </p>
        </div>
      )}
    </section>
  )
}
