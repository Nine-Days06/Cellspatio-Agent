import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MessageList, EMPTY_FALLBACK } from '@/components/MessageList'
import type { ToolStatusEntry } from '@/components/ToolStatus'
import type { PendingScript, WireMessage } from '@/lib/types'

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
  notice: null as { kind: 'info' | 'error'; text: string } | null,
  softWarn: false,
  onRetry: vi.fn(),
}

afterEach(() => {
  vi.restoreAllMocks()
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

describe('流式与过程条', () => {
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
  })
})
