import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MessageList, EMPTY_FALLBACK } from '@/components/MessageList'
import type { ToolStatusEntry } from '@/components/ToolStatus'
import type { ChatMode, PendingScript, WireMessage } from '@/lib/types'
import { emptyReasoning, type ReasoningState } from '@/store/chat'

function reasoningState(overrides: Partial<ReasoningState> = {}): ReasoningState {
  return { ...emptyReasoning(), ...overrides }
}

function msg(overrides: Partial<WireMessage> = {}): WireMessage {
  return {
    id: 1,
    session_id: 's1',
    seq: 1,
    role: 'assistant',
    content: '',
    timestamp: '2026-09-30T00:00:00',
    results: null,
    pending_script: null,
    ...overrides,
  }
}

const baseProps = {
  messages: [] as WireMessage[],
  draft: '',
  busy: false,
  tools: [] as ToolStatusEntry[],
  mode: 'chat' as ChatMode,
  notice: null as { kind: 'info' | 'error'; text: string } | null,
  softWarn: false,
  onRetry: vi.fn(),
}

const originalMatchMedia = window.matchMedia
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView

afterEach(() => {
  vi.restoreAllMocks()
  window.matchMedia = originalMatchMedia
  HTMLElement.prototype.scrollIntoView = originalScrollIntoView
})

describe('空内容兜底（约束 1）', () => {
  it('空内容且无图表 → 显示兜底文案与重试按钮', () => {
    render(
      <MessageList {...baseProps} messages={[msg({ content: '', results: { charts: [] } })]} />,
    )
    expect(screen.getByText(EMPTY_FALLBACK)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('有图表时不显示兜底文案', () => {
    render(
      <MessageList
        {...baseProps}
        messages={[
          msg({
            content: '',
            results: { charts: [{ type: 'image', image_b64: 'eHl6' }] },
          }),
        ]}
      />,
    )
    expect(screen.queryByText(EMPTY_FALLBACK)).not.toBeInTheDocument()
  })

  it('有文本时不显示兜底文案', () => {
    render(<MessageList {...baseProps} messages={[msg({ content: '有内容' })]} />)
    expect(screen.queryByText(EMPTY_FALLBACK)).not.toBeInTheDocument()
  })

  it('有卡片（pending_script）时不显示兜底文案', () => {
    const pendingScript: PendingScript = {
      script: 'DESeq2 代码',
      analysis_type: 'bulk_de',
      params: {},
      method_context: null,
      user_request: '跑差异表达',
      status: 'pending',
    }
    render(
      <MessageList {...baseProps} messages={[msg({ content: '', pending_script: pendingScript })]} />,
    )
    expect(screen.queryByText(EMPTY_FALLBACK)).not.toBeInTheDocument()
  })

  it('有文献证据包时不显示兜底文案，并渲染可展开证据', () => {
    render(
      <MessageList
        {...baseProps}
        messages={[
          msg({
            content: '',
            results: {
              knowledge_evidence: {
                outcome: 'citation_invalid',
                evidence: [{ evidence_id: 'E1', title: 'A paper', content: '证据原文' }],
                sources: [],
                citations: [],
                claims: [],
              },
            },
          }),
        ]}
      />,
    )

    expect(screen.queryByText(EMPTY_FALLBACK)).not.toBeInTheDocument()
    expect(screen.getByText('文献证据（1 条）')).toBeInTheDocument()
    expect(screen.getByText('证据原文')).toBeInTheDocument()
  })

  it('点击重试触发 onRetry', () => {
    const onRetry = vi.fn()
    render(
      <MessageList
        {...baseProps}
        onRetry={onRetry}
        messages={[msg({ content: '' })]}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})

describe('统一思考条', () => {
  it('历史消息带绑定思考时渲染在正文之前', () => {
    render(
      <MessageList
        {...baseProps}
        messages={[msg({ id: 9, content: '正式回答' })]}
        reasoning={reasoningState({ text: '先分析问题', elapsedMs: 3000, messageId: 9 })}
      />,
    )

    const panel = screen.getByTestId('reasoning-panel')
    const answer = screen.getByText('正式回答')
    expect(panel).toBeInTheDocument()
    expect(screen.getByText('已思考 3 秒')).toBeInTheDocument()
    expect(panel.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('流式阶段只显示一条思考条，不再显示「思考中」兜底', () => {
    render(
      <MessageList
        {...baseProps}
        busy
        reasoning={reasoningState({ text: '正在思考', startedAt: Date.now() - 2000 })}
      />,
    )

    expect(screen.getByTestId('reasoning-panel')).toBeInTheDocument()
    expect(screen.getAllByText('思考中')).toHaveLength(1)
  })

  it('没有思考内容时不渲染思考条', () => {
    render(<MessageList {...baseProps} messages={[msg({ content: '回答' })]} />)
    expect(screen.queryByTestId('reasoning-panel')).not.toBeInTheDocument()
  })

  it('知识问答历史消息同样显示绑定的思考条', () => {
    render(
      <MessageList
        {...baseProps}
        mode="knowledge_qa"
        messages={[
          msg({
            id: 11,
            content: '证据回答',
            results: {
              knowledge_qa: {
                answer_id: 'qa-1',
                schema_version: 'knowledge-qa/2.0.0',
                status: 'success',
                outcome: 'supported_answer',
                answer: '证据回答',
                claims: [],
                evidence: [],
                sources: [],
                limitations: [],
                clarifications: [],
              },
            },
          }),
        ]}
        reasoning={reasoningState({ text: '先检索再作答', elapsedMs: 2000, messageId: 11 })}
      />,
    )

    expect(screen.getByTestId('reasoning-panel')).toBeInTheDocument()
    expect(screen.getByText('已思考 2 秒')).toBeInTheDocument()
  })
})

describe('流式与过程条', () => {
  it('读历史时流式输出不抢滚动；回到最新后恢复跟随', () => {
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView })
    const { rerender } = render(<MessageList {...baseProps} draft="开始" busy />)
    const list = screen.getByTestId('message-list')
    Object.defineProperties(list, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 300 },
      scrollTop: { configurable: true, writable: true, value: 100 },
    })
    fireEvent.scroll(list)
    scrollIntoView.mockClear()
    rerender(<MessageList {...baseProps} draft="更新的内容" busy />)
    expect(scrollIntoView).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '回到最新内容' }))
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    rerender(<MessageList {...baseProps} draft="继续更新" busy />)
    expect(scrollIntoView).toHaveBeenCalledTimes(2)
    delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView
  })

  it('起步提示只交给输入区，并携带相应模式', () => {
    const onStarter = vi.fn()
    render(<MessageList {...baseProps} onStarter={onStarter} />)
    fireEvent.click(screen.getByRole('button', { name: /查阅文献证据/ }))
    expect(onStarter).toHaveBeenCalledWith(expect.stringContaining('scRNA-seq'), 'knowledge_qa')
  })
  it('draft 非空时渲染流式气泡', () => {
    render(<MessageList {...baseProps} draft="正在分析" busy />)
    expect(screen.getByText('正在分析')).toBeInTheDocument()
  })

  it('展示 tool_status 过程条文案', () => {
    const tools: ToolStatusEntry[] = [
      { name: 'run_analysis', phase: 'start', label: '运行分析' },
    ]
    render(<MessageList {...baseProps} busy tools={tools} />)
    expect(screen.getByText('运行分析')).toBeInTheDocument()
  })

  it('展示检索结束后的证据条数', () => {
    const tools: ToolStatusEntry[] = [
      { name: 'search_knowledge', phase: 'end', label: '检索文献', count: 3 },
    ]
    render(<MessageList {...baseProps} busy tools={tools} />)
    expect(screen.getByText('检索文献：3 条证据')).toBeInTheDocument()
  })

  it('soft_warn 展示横幅', () => {
    render(<MessageList {...baseProps} softWarn />)
    expect(screen.getByText(/上下文接近压缩阈值/)).toBeInTheDocument()
  })

  it('notice 展示错误提示', () => {
    render(
      <MessageList {...baseProps} notice={{ kind: 'error', text: '该会话已有任务在进行' }} />,
    )
    expect(screen.getByText('该会话已有任务在进行')).toBeInTheDocument()
  })
})

describe('过程条不得冒充终态（约束 2）', () => {
  it('只有 start 过程条：只显示文案，不出现完成语义', () => {
    const tools: ToolStatusEntry[] = [
      { name: 'run_analysis', phase: 'start', label: '运行分析' },
    ]
    render(<MessageList {...baseProps} busy tools={tools} />)
    expect(screen.getByText('运行分析')).toBeInTheDocument()
    expect(screen.queryByText(/完成|已完成|已结束|成功/)).not.toBeInTheDocument()
  })

  it('后端不保证发 end：phase 为 end 的过程条不渲染任何终态文案', () => {
    const tools: ToolStatusEntry[] = [
      { name: 'run_analysis', phase: 'end', label: '运行分析' },
    ]
    render(<MessageList {...baseProps} busy tools={tools} />)
    expect(screen.queryByText(/运行分析/)).not.toBeInTheDocument()
    expect(screen.queryByText(/完成|已完成|已结束|成功/)).not.toBeInTheDocument()
  })
})

describe('单一状态文案', () => {
  it('知识问答检索阶段只显示「检索知识库」', () => {
    const tools: ToolStatusEntry[] = [
      { name: 'knowledge_qa_retrieval', phase: 'start', label: '检索知识库' },
    ]
    render(<MessageList {...baseProps} busy mode="knowledge_qa" tools={tools} />)

    expect(screen.getByText('检索知识库')).toBeInTheDocument()
    expect(screen.queryByText('准备回答')).not.toBeInTheDocument()
    expect(screen.queryByText('思考中')).not.toBeInTheDocument()
    expect(screen.queryByText('生成中…')).not.toBeInTheDocument()
  })

  it('知识问答生成阶段只显示「生成回答」', () => {
    const tools: ToolStatusEntry[] = [
      { name: 'knowledge_qa_retrieval', phase: 'end', label: '检索知识库' },
      { name: 'knowledge_qa_answer', phase: 'start', label: '生成回答' },
    ]
    render(<MessageList {...baseProps} busy mode="knowledge_qa" tools={tools} />)

    expect(screen.getByText('生成回答')).toBeInTheDocument()
    expect(screen.queryByText('检索知识库')).not.toBeInTheDocument()
    expect(screen.queryByText('准备回答')).not.toBeInTheDocument()
  })

  it('阶段结束后知识问答显示「准备回答」', () => {
    const tools: ToolStatusEntry[] = [
      { name: 'knowledge_qa_answer', phase: 'end', label: '生成回答' },
    ]
    render(<MessageList {...baseProps} busy mode="knowledge_qa" tools={tools} />)

    expect(screen.getByText('准备回答')).toBeInTheDocument()
    expect(screen.queryByText('生成回答')).not.toBeInTheDocument()
  })

  it('正文开始出现后阶段与兜底文案都不显示', () => {
    const tools: ToolStatusEntry[] = [
      { name: 'knowledge_qa_answer', phase: 'start', label: '生成回答' },
    ]
    render(<MessageList {...baseProps} busy mode="knowledge_qa" tools={tools} draft="回答正文" />)

    expect(screen.getByText('回答正文')).toBeInTheDocument()
    expect(screen.queryByText('生成回答')).not.toBeInTheDocument()
    expect(screen.queryByText('准备回答')).not.toBeInTheDocument()
  })

  it('普通对话无正文且无活动工具时显示「思考中」', () => {
    render(<MessageList {...baseProps} busy />)
    expect(screen.getByText('思考中')).toBeInTheDocument()
  })
})

describe('Markdown 惰性渲染与代码块复制', () => {
  const OriginalIO = globalThis.IntersectionObserver

  afterEach(() => {
    globalThis.IntersectionObserver = OriginalIO
  })

  it('未进入视口时降级为纯文本，进入后渲染富文本', () => {
    // 永不回调的 IO：模拟「还没滚到」
    globalThis.IntersectionObserver = class {
      root = null
      rootMargin = ''
      thresholds: ReadonlyArray<number> = []
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return []
      }
    } as unknown as typeof IntersectionObserver

    render(<MessageList {...baseProps} messages={[msg({ content: '## 标题\n**加粗**' })]} />)

    expect(screen.getByText('## 标题')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 2 })).not.toBeInTheDocument()
  })

  it('代码块可一键复制', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
      writable: true,
    })

    render(
      <MessageList
        {...baseProps}
        messages={[msg({ content: '```r\nx <- 1\n```' })]}
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: '复制' }))
    expect(writeText).toHaveBeenCalledWith('x <- 1')
    expect(document.querySelector('code.hljs')).not.toBeNull()
  })

  it('剪贴板失败不假报已复制', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    render(<MessageList {...baseProps} messages={[msg({ content: '```r\nx <- 1\n```' })]} />)
    fireEvent.click(await screen.findByRole('button', { name: '复制' }))
    expect(await screen.findByRole('status')).toHaveTextContent('无法复制')
    expect(screen.queryByText('已复制')).not.toBeInTheDocument()
  })
})

describe('新内容入场标记', () => {
  it('历史消息不带入场标记', () => {
    render(
      <MessageList
        {...baseProps}
        messages={[
          msg({ id: 5, role: 'user', content: '旧问题' }),
          msg({ id: 6, content: '旧回答' }),
        ]}
      />,
    )
    expect(document.querySelectorAll('[data-motion-enter]')).toHaveLength(0)
  })

  it('追加本轮用户消息后只有该条带标记', () => {
    const history = msg({ id: 5, role: 'user', content: '旧问题' })
    const { rerender } = render(<MessageList {...baseProps} messages={[history]} />)
    rerender(
      <MessageList
        {...baseProps}
        messages={[history, msg({ id: -1, seq: -1, role: 'user', content: '新问题' })]}
      />,
    )
    const marked = document.querySelectorAll('[data-motion-enter]')
    expect(marked).toHaveLength(1)
    expect(marked[0]).toHaveTextContent('新问题')
    expect(marked[0].querySelector('.motion-enter-up')).not.toBeNull()
  })

  it('历史未加载完成时本地消息不播入场', () => {
    render(
      <MessageList
        {...baseProps}
        historyReady={false}
        messages={[msg({ id: -1, seq: -1, role: 'user', content: '新问题' })]}
      />,
    )
    expect(document.querySelectorAll('[data-motion-enter]')).toHaveLength(0)
  })

  it('活动气泡带标记，落历史后只渲染一份，下一轮开始时不重挂', () => {
    const final = msg({ id: 21, content: '回答正文' })
    const { rerender } = render(
      <MessageList
        {...baseProps}
        busy
        draft="回答正文"
        reasoning={reasoningState({ text: '先想', startedAt: Date.now() })}
      />,
    )
    expect(document.querySelectorAll('[data-motion-enter]')).toHaveLength(1)

    rerender(
      <MessageList
        {...baseProps}
        messages={[final]}
        reasoning={reasoningState({ text: '先想', elapsedMs: 1000, messageId: 21 })}
      />,
    )
    expect(screen.getAllByTestId('reasoning-panel')).toHaveLength(1)
    expect(document.querySelectorAll('[data-motion-enter]')).toHaveLength(0)
    const body = screen.getByText('回答正文')

    // 下一轮开始：reasoning 被清空，历史消息的 key 必须稳定，展开状态不被重置。
    rerender(<MessageList {...baseProps} messages={[final]} reasoning={reasoningState({})} />)
    expect(screen.getByText('回答正文')).toBe(body)
  })
})


describe('流式光标与回到最新内容', () => {
  it('光标只在生成中显示，aria-hidden 且正文不含光标文本', () => {
    render(<MessageList {...baseProps} busy draft="正在输出的正文" />)

    const caret = screen.getByTestId('stream-caret')
    expect(caret).toHaveAttribute('aria-hidden', 'true')
    expect(caret.className).toContain('motion-caret')
    expect(caret).not.toHaveTextContent('正在输出的正文')
    expect(screen.getByText('正在生成回答')).toBeInTheDocument()
    expect(screen.getByText('正在输出的正文')).toBeInTheDocument()
  })

  it('没开始输出或已收尾时不显示光标', () => {
    const { rerender } = render(<MessageList {...baseProps} busy />)
    expect(screen.queryByTestId('stream-caret')).toBeNull()

    rerender(<MessageList {...baseProps} draft="已收尾的正文" />)
    expect(screen.queryByTestId('stream-caret')).toBeNull()
  })

  it('回到最新内容默认平滑滚动', () => {
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView })
    render(<MessageList {...baseProps} messages={[msg({ id: 1, content: '旧回答' })]} />)
    const host = screen.getByTestId('message-list')
    Object.defineProperties(host, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 300 },
      scrollTop: { configurable: true, writable: true, value: 100 },
    })
    fireEvent.scroll(host)
    fireEvent.click(screen.getByRole('button', { name: '回到最新内容' }))
    expect(scrollIntoView).toHaveBeenLastCalledWith(expect.objectContaining({ behavior: 'smooth' }))
  })

  it('减少动效时即时滚动，且按钮隐藏时不进可访问树', () => {
    expect(screen.queryByRole('button', { name: '回到最新内容' })).toBeNull()
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView })
    window.matchMedia = vi.fn().mockReturnValue({ matches: true }) as unknown as typeof window.matchMedia
    render(<MessageList {...baseProps} messages={[msg({ id: 1, content: '旧回答' })]} />)
    const host = screen.getByTestId('message-list')
    Object.defineProperties(host, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 300 },
      scrollTop: { configurable: true, writable: true, value: 100 },
    })
    fireEvent.scroll(host)
    fireEvent.click(screen.getByRole('button', { name: '回到最新内容' }))
    expect(scrollIntoView).toHaveBeenLastCalledWith(expect.objectContaining({ behavior: 'auto' }))
    delete (window as { matchMedia?: typeof window.matchMedia }).matchMedia
    delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView
  })
})

describe('工具阶段动效', () => {
  it('状态行整体淡入，文案切换用 key 短淡入且行结构不变', () => {
    const first: ToolStatusEntry[] = [{ name: 'a', phase: 'start', label: '检索知识库' }]
    const { rerender } = render(<MessageList {...baseProps} busy tools={first} />)

    const row = screen.getByTestId('tool-status')
    expect(row.className).toContain('motion-fade-fast')
    const label = screen.getByText('检索知识库')
    expect(label.className).toContain('motion-swap')

    rerender(
      <MessageList
        {...baseProps}
        busy
        tools={[{ name: 'b', phase: 'start', label: '生成回答' }]}
      />,
    )
    expect(screen.getByTestId('tool-status')).toBe(row)
    expect(label).not.toBeInTheDocument()
    expect(screen.getByText('生成回答').className).toContain('motion-swap')
  })
})
