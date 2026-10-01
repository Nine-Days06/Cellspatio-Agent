import { useEffect, useRef, type ReactNode } from 'react'
import { ChartBlock } from '@/components/ChartBlock'
import { MarkdownMessage } from '@/components/MarkdownMessage'
import { SoftWarnBanner } from '@/components/SoftWarnBanner'
import { ToolStatus, type ToolStatusEntry } from '@/components/ToolStatus'
import type { WireMessage } from '@/lib/types'
import { cn } from '@/lib/utils'

/** 约束 1：知识库不可用时 done.message.content 可能为空字符串，需要兜底文案。 */
export const EMPTY_FALLBACK = '本次未产生内容，请重试或换个问法'

export interface MessageListProps {
  messages: WireMessage[]
  draft: string
  busy: boolean
  tools: ToolStatusEntry[]
  notice: { kind: 'info' | 'error'; text: string } | null
  softWarn: boolean
  onRetry?: () => void
}

/** 空内容判定的「视觉内容」：图表或消息内嵌的待确认脚本卡片，任一存在都不兜底（约束 1）。 */
function hasVisuals(message: WireMessage): boolean {
  return (message.results?.charts?.length ?? 0) > 0 || Boolean(message.pending_script)
}

function Bubble({ role, children }: { role: 'user' | 'assistant'; children: ReactNode }) {
  return (
    <div className={cn('flex', role === 'user' && 'justify-end')}>
      <div
        className={cn(
          'max-w-[85%] rounded-2xl px-3 py-2',
          role === 'user'
            ? 'bg-primary text-primary-foreground'
            : 'border border-border bg-card text-card-foreground',
        )}
      >
        {children}
      </div>
    </div>
  )
}

export function MessageList({
  messages,
  draft,
  busy,
  tools,
  notice,
  softWarn,
  onRetry,
}: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null)
  const lastTool = tools.length > 0 ? tools[tools.length - 1] : null

  useEffect(() => {
    // jsdom 未实现 scrollIntoView，可选调用保证单测不炸；浏览器里照常滚动
    endRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages.length, draft, tools.length])

  return (
    <div className="flex-1 overflow-y-auto px-4 py-3" data-testid="message-list">
      <SoftWarnBanner visible={softWarn} />
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
        {messages.map((message) => {
          const empty =
            !message.content.trim() && !hasVisuals(message) && message.role === 'assistant'
          return (
            <Bubble key={`${message.id}-${message.seq}`} role={message.role}>
              {message.role === 'assistant' ? (
                <>
                  {empty ? (
                    <div className="flex flex-col gap-2 text-sm">
                      <span>{EMPTY_FALLBACK}</span>
                      {onRetry && (
                        <button
                          type="button"
                          onClick={onRetry}
                          className="self-start rounded-lg border border-border px-2 py-1 text-xs hover:bg-muted"
                        >
                          重试
                        </button>
                      )}
                    </div>
                  ) : (
                    <MarkdownMessage text={message.content} />
                  )}
                  {(message.results?.charts ?? []).length > 0 && (
                    <div data-testid="charts-slot" className="mt-2">
                      {(message.results?.charts ?? []).map((chart, idx) => (
                        <ChartBlock key={`${chart.type}-${idx}`} item={chart} />
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <div className="whitespace-pre-wrap text-sm">{message.content}</div>
              )}
            </Bubble>
          )
        })}

        {(draft.length > 0 || busy) && (
          <Bubble role="assistant">
            <ToolStatus entry={lastTool} />
            {draft.length > 0 ? (
              <MarkdownMessage text={draft} />
            ) : (
              <span className="text-sm text-muted-foreground">思考中…</span>
            )}
          </Bubble>
        )}

        {notice && (
          <p
            role="status"
            className={cn(
              'rounded-lg border px-3 py-2 text-sm',
              notice.kind === 'error'
                ? 'border-destructive/40 bg-destructive/10 text-destructive'
                : 'border-border bg-muted text-muted-foreground',
            )}
          >
            {notice.text}
          </p>
        )}
        <div ref={endRef} />
      </div>
    </div>
  )
}
