import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUpRight, FlaskConical, BookOpen, Scan } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ChartBlock } from '@/components/ChartBlock'
import { KnowledgeAnswer } from '@/components/KnowledgeAnswer'
import {
  KnowledgeEvidence,
  linkKnowledgeCitations,
} from '@/components/KnowledgeEvidence'
import { QcEvidence } from '@/components/QcEvidence'
import { MarkdownMessage } from '@/components/MarkdownMessage'
import { ReasoningPanel } from '@/components/ReasoningPanel'
import { SoftWarnBanner } from '@/components/SoftWarnBanner'
import { ToolStatus, type ToolStatusEntry } from '@/components/ToolStatus'
import { emptyReasoning, type ReasoningState } from '@/store/chat'
import type { ChatMode, WireMessage } from '@/lib/types'
import { prefersReducedMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'

/** 约束 1：知识库不可用时 done.message.content 可能为空字符串，需要兜底文案。 */
export const EMPTY_FALLBACK = '本次未产生内容，请重试或换个问法'

const NO_REASONING = emptyReasoning()

const LIVE_KEY = 'live-turn'
function noop(): void {}

export interface MessageListProps {
  messages: WireMessage[]
  /** 会话首次历史加载完成；未完成前到达的内容一律视为已见，不播入场。 */
  historyReady?: boolean
  draft: string
  busy: boolean
  tools: ToolStatusEntry[]
  mode: ChatMode
  /** 停止/慢提示在输入区独立显示时，隐藏气泡内的阶段与兜底文案。 */
  hideStatus?: boolean
  notice: { kind: 'info' | 'error'; text: string } | null
  softWarn: boolean
  /** 本轮思考状态；只在内存中展示，不进入消息正文。 */
  reasoning?: ReasoningState
  onToggleReasoning?: () => void
  onRetry?: () => void
  onQcRecorded?: () => void
  onStarter?: (prompt: string, mode: ChatMode) => void
}

/** 图表、QC 证据或待确认脚本卡片存在时，不用空消息兜底。 */
function hasContent(message: WireMessage): boolean {
  return (message.results?.charts?.length ?? 0) > 0 || Boolean(message.pending_script) || Boolean(message.results?.qc_artifacts) || Boolean(message.results?.knowledge_qa) || Boolean(message.results?.knowledge_evidence?.evidence?.length)
}

function Bubble({
  role,
  children,
  entering,
}: {
  role: 'user' | 'assistant'
  children: ReactNode
  /** 仅本轮新增内容挂载；历史、恢复与刷新不挂。 */
  entering?: boolean
}) {
  return (
    <div className={cn('flex', role === 'user' && 'justify-end')} data-motion-enter={entering ? '' : undefined}>
      <div
        className={cn(
          'min-w-0',
          entering && 'motion-enter-up',
          role === 'user'
            ? 'user-message max-w-[90%] rounded-2xl rounded-br-sm px-4 py-3 text-foreground'
            : 'w-full text-foreground',
        )}
      >
        {children}
      </div>
    </div>
  )
}

export function MessageList({
  messages,
  historyReady = true,
  draft,
  busy,
  tools,
  mode,
  hideStatus = false,
  notice,
  softWarn,
  reasoning = NO_REASONING,
  onToggleReasoning = noop,
  onRetry,
  onQcRecorded,
  onStarter,
}: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const [awayFromLatest, setAwayFromLatest] = useState(false)
  const lastTool = tools.length > 0 ? tools[tools.length - 1] : null
  const hasReasoning = reasoning.text.trim().length > 0
  /** 已挂到历史消息上的思考由消息自己渲染，活动气泡不再重复显示。 */
  const liveReasoning = hasReasoning && reasoning.messageId === null
  /** 同一时刻只显示一条状态：有活动阶段用阶段文案，否则按模式兜底；正文出现后都不显示。 */
  const showStatus = draft.length === 0 && !hideStatus
  const statusEntry = showStatus ? lastTool : null
  const statusFallback =
    showStatus && busy && !liveReasoning
      ? mode === 'knowledge_qa'
        ? '准备回答'
        : '思考中'
      : null

  /** 活动气泡完成后改由消息渲染；两边共用同一 key，落历史时不重新挂载。 */
  /** 历史与活动气泡放进同一数组，活动气泡独占一个常量 key。 */
  const bubbles: ReactNode[] = messages.map((message) => {
    const messageReasoning =
      message.role === 'assistant' &&
      hasReasoning &&
      message.id === reasoning.messageId
    const knowledge = message.results?.knowledge_evidence
    const empty =
      !message.content.trim() &&
      !hasContent(message) &&
      !messageReasoning &&
      message.role === 'assistant'
    const fresh = historyReady && message.role === 'user' && message.id < 0
    return (
      <Bubble
        key={`${message.id}-${message.seq}`}
        role={message.role}
        entering={fresh}
      >
        {message.role === 'assistant' ? (
          <>
            {messageReasoning && (
              <ReasoningPanel
                text={reasoning.text}
                running={false}
                elapsedMs={reasoning.elapsedMs}
                truncated={reasoning.truncated}
                expanded={reasoning.expanded}
                onToggle={onToggleReasoning}
              />
            )}
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
            ) : !message.results?.knowledge_qa ? (
              <MarkdownMessage
                text={
                  knowledge
                    ? linkKnowledgeCitations(
                        message.content,
                        knowledge.citations,
                        message.id,
                      )
                    : message.content
                }
                className="reading-prose"
              />
            ) : null}
            {(message.results?.charts ?? []).length > 0 && (
              <div data-testid="charts-slot" className="mt-2">
                {(message.results?.charts ?? []).map((chart, idx) => (
                  <ChartBlock key={`${chart.type}-${idx}`} item={chart} />
                ))}
              </div>
            )}
            {message.results?.qc_artifacts && <QcEvidence results={message.results} sessionId={message.session_id} onRecorded={onQcRecorded} />}
            {/* 旧会话兼容：新消息一律使用 results.knowledge_evidence。 */}
            {message.results?.knowledge_qa && <KnowledgeAnswer data={message.results.knowledge_qa} text={message.content} />}
            {knowledge && <KnowledgeEvidence data={knowledge} messageId={message.id} />}
          </>
        ) : (
          <div className="whitespace-pre-wrap text-sm">{message.content}</div>
        )}
      </Bubble>
    )
  })
  /** 消息已接管本轮思考/正文后，活动气泡必须让位，避免同 key 并存与完成态误报「思考中」。 */
  const ownedByMessage = reasoning.messageId !== null && messages.some((m) => m.id === reasoning.messageId)
  if (!ownedByMessage && (draft.length > 0 || liveReasoning || (busy && !hideStatus))) {
    bubbles.push(
      <Bubble key={LIVE_KEY} role="assistant" entering>
        {liveReasoning && (
          <ReasoningPanel
            text={reasoning.text}
            running={reasoning.startedAt !== null}
            startedAt={reasoning.startedAt}
            elapsedMs={reasoning.elapsedMs}
            truncated={reasoning.truncated}
            expanded={reasoning.expanded}
            onToggle={onToggleReasoning}
          />
        )}
        <ToolStatus entry={statusEntry} fallback={statusFallback} />
        {draft.length > 0 && (
          <div className="relative">
            <MarkdownMessage text={draft} className="reading-prose" />
            {busy && (
              <>
                {/* 光标在正文之外，绝对定位不占行高；正文继续输出时位置自然下移。 */}
                <span
                  data-testid="stream-caret"
                  aria-hidden="true"
            className="motion-caret pointer-events-none absolute left-0 top-full mt-1 h-[1.1em] w-0.5 rounded-full bg-current opacity-70"
                />
                <span className="sr-only">正在生成回答</span>
              </>
            )}
          </div>
        )}
      </Bubble>,
    )
  }

  useEffect(() => {
    // 只跟随正在读最新内容的人；流式更新不抢走历史阅读位置。
    if (following.current) endRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages.length, draft, reasoning.text, tools.length, notice])

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
    <div ref={scrollRef} className="record-scroll min-h-0 flex-1 overflow-y-auto px-5 py-6 md:px-9" data-testid="message-list" onScroll={() => {
      const host = scrollRef.current
      if (!host) return
      following.current = host.scrollHeight - host.scrollTop - host.clientHeight < 96
      setAwayFromLatest(!following.current)
    }}>
      <SoftWarnBanner visible={softWarn} />
      <div className="mx-auto flex max-w-6xl flex-col gap-9">
        {messages.length === 0 && !busy && !draft && (
          <section className="workspace-start max-w-3xl py-6 md:py-12" aria-label="开始对话">
            <h2 className="max-w-xl text-3xl font-semibold leading-tight tracking-tight md:text-4xl">从一个数据问题开始</h2>
            <p className="mt-4 max-w-xl leading-7 text-muted-foreground">把样本背景、数据与目标放在一起。先理解证据，再决定下一步分析。</p>
            {onStarter && <div className="mt-8 divide-y divide-border border-y border-border">
              {[
                { icon: FlaskConical, label: '检查单细胞数据', detail: '从输入文件、样本背景和 QC 开始', prompt: '我有一份人类单细胞 10x 数据，请先帮我确认输入格式和 QC 需要的样本背景。', mode: 'chat' as const },
                { icon: BookOpen, label: '查阅文献证据', detail: '比较方法、适用条件与证据限制', prompt: 'scRNA-seq 与 snRNA-seq 有什么区别？各自适用于哪些样本？', mode: 'knowledge_qa' as const },
                { icon: Scan, label: '规划空间组学分析', detail: '说明技术平台、组织和研究问题', prompt: '我想分析人类空间转录组数据，需要先提供哪些平台、组织和研究背景信息？', mode: 'chat' as const },
              ].map((item) => <button key={item.label} type="button" onClick={() => onStarter(item.prompt, item.mode)} className="starter-row flex w-full items-center gap-4 py-4 text-left hover:bg-accent/40">
                <item.icon className="size-5 shrink-0 text-primary" aria-hidden="true" />
                <span className="min-w-0 flex-1"><span className="block font-medium">{item.label}</span><span className="mt-1 block text-sm text-muted-foreground">{item.detail}</span></span>
                <ArrowUpRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              </button>)}
            </div>}
            <p className="mt-6 max-w-xl text-xs leading-6 text-muted-foreground">{onStarter && '点击提示只填入问题，不会发送。'}分析执行需要明确授权；保存选择不等于执行，也不代表科学认可。</p>
          </section>
        )}
        {bubbles}

        {notice && (
          <p
            role={notice.kind === 'error' ? 'alert' : 'status'}
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
    <Button variant="outline" tabIndex={awayFromLatest ? undefined : -1} aria-hidden={!awayFromLatest} className={cn('motion-float absolute bottom-4 left-1/2 min-h-11 -translate-x-1/2 rounded-full bg-card shadow-sm', !awayFromLatest && 'motion-float-hidden')} onClick={() => {
      following.current = true
      setAwayFromLatest(false)
      endRef.current?.scrollIntoView?.({ block: 'end', behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
    }}><ArrowDown className="size-4" aria-hidden="true" />回到最新内容</Button>
    </div>
  )
}

