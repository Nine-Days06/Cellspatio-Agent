/** ChatPage 接线测试（T8）。
 *
 * Mock 架构（契约优先，与计划草图的过期写法不同）：
 * - `@/lib/api` 部分 mock：保留真实 `fetchMessages`（?restore=1「首次成功才带」的
 *   逻辑在 api.ts 内部，ChatPage 只需用 useMessages 触发）与真实 `ApiError`；
 * - `@/lib/sse` 部分 mock：只拦截 `streamChat`，其余导出原样保留；
 * - queries 走真实实现 + 每用例独立 QueryClient；全局 fetch 只服务 /messages，
 *   按 sid 路由载荷，从而真实验证 restore=1 与 hydrate 链路。
 *
 * 会话 id 每用例唯一：api.ts 模块级 `restoredSessions` Set 跨用例存活，
 * 同一 sid 的第二次拉取不再带 restore=1。
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EMPTY_FALLBACK } from '@/components/MessageList'
import { ChatPage } from '@/pages/ChatPage'
import { ApiError } from '@/lib/api'
import type {
  DataCardPayload,
  MessagesResponse,
  PendingScript,
  ScriptCardPayload,
  SessionSummary,
  WireMessage,
} from '@/lib/types'
import { useChatStore } from '@/store/chat'

// vi.mock 工厂会被提升到 import 之前：工厂体内只能以闭包延迟引用这些函数，
// 测试体内则直接使用。工厂执行期（import 阶段）不读取变量，无 TDZ 风险。
const listSessionsMock = vi.fn()
const fetchSidebarMock = vi.fn()
const confirmActionMock = vi.fn()
const createSessionMock = vi.fn()
const deleteSessionMock = vi.fn()
const streamChatMock = vi.fn()
const fetchMock = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    listSessions: (...args: unknown[]) => listSessionsMock(...args),
    fetchSidebar: (...args: unknown[]) => fetchSidebarMock(...args),
    confirmAction: (...args: unknown[]) => confirmActionMock(...args),
    createSession: (...args: unknown[]) => createSessionMock(...args),
    deleteSession: (...args: unknown[]) => deleteSessionMock(...args),
  }
})

vi.mock('@/lib/sse', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/sse')>()
  return {
    ...actual,
    streamChat: (...args: unknown[]) => streamChatMock(...args),
  }
})

/** 按 sid 存放 /messages 响应；未 seed 的 sid 返回空历史（session.id 与请求一致）。 */
const payloads = new Map<string, MessagesResponse>()

function sessionFor(sid: string): MessagesResponse['session'] {
  return {
    id: sid,
    title: '测试会话',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    summary: null,
    summary_upto: 0,
    downloaded_assets: [],
  }
}

function seedMessages(
  sid: string,
  messages: WireMessage[],
  extra: Partial<MessagesResponse> = {},
): void {
  payloads.set(sid, {
    session: sessionFor(sid),
    messages,
    pending_script: null,
    soft_warn: false,
    ...extra,
  })
}

function routeFetch(url: unknown): Promise<unknown> {
  const match = /\/sessions\/([^/]+)\/messages/.exec(String(url))
  if (!match) {
    return Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: '未找到' }) })
  }
  const sid = decodeURIComponent(match[1])
  const payload =
    payloads.get(sid) ??
    ({ session: sessionFor(sid), messages: [], pending_script: null, soft_warn: false } as MessagesResponse)
  return Promise.resolve({ ok: true, status: 200, json: async () => payload })
}

let sidSeq = 0
function nextSid(): string {
  sidSeq += 1
  return `t${sidSeq}`
}

let msgSeq = 0
function makeMsg(sid: string, overrides: Partial<WireMessage> = {}): WireMessage {
  msgSeq += 1
  return {
    id: msgSeq,
    session_id: sid,
    seq: msgSeq,
    role: 'assistant',
    content: '历史回答',
    timestamp: '2026-10-01T00:00:00Z',
    results: null,
    pending_script: null,
    ...overrides,
  }
}

function makeSummary(id: string, title = '测试会话'): SessionSummary {
  return { id, title, updated_at: '2026-10-01T00:00:00Z', message_count: 0 }
}

function pendingCard(overrides: Partial<ScriptCardPayload> = {}): ScriptCardPayload {
  return {
    message_id: 7,
    script: 'x <- 1',
    analysis_type: null,
    params: {},
    method_context: null,
    user_request: '跑分析',
    status: 'pending',
    ...overrides,
  }
}

interface ReadyOptions {
  messages?: WireMessage[]
  extra?: Partial<MessagesResponse>
  sessions?: SessionSummary[]
}

/** 渲染 ChatPage 并等待「自动选中首个会话 → 拉历史 → hydrate」完成。 */
async function renderReady(sid: string, options: ReadyOptions = {}): Promise<void> {
  const messages = options.messages ?? [makeMsg(sid)]
  seedMessages(sid, messages, options.extra)
  listSessionsMock.mockResolvedValue({ sessions: options.sessions ?? [makeSummary(sid)] })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ChatPage />
    </QueryClientProvider>,
  )
  // history 深等于种子载荷 = hydrate 已落地（初始 history 为空，不会误判）
  await waitFor(() => expect(useChatStore.getState().history).toEqual(messages))
}

beforeEach(() => {
  useChatStore.setState({
    sessionId: null,
    history: [],
    draft: '',
    busy: false,
    tools: [],
    notice: null,
    charts: [],
    scriptCard: null,
    dataCard: null,
    softWarn: false,
  })
  listSessionsMock.mockReset()
  fetchSidebarMock.mockReset()
  confirmActionMock.mockReset()
  createSessionMock.mockReset()
  deleteSessionMock.mockReset()
  streamChatMock.mockReset()
  fetchMock.mockReset()
  // 默认挂起流：模拟「进行中不返回」的长任务，用于约束 2/3 断言
  streamChatMock.mockImplementation(() => new Promise<void>(() => {}))
  listSessionsMock.mockResolvedValue({ sessions: [] })
  fetchSidebarMock.mockResolvedValue({
    kb_stats: { initialized: true },
    env: {
      rscript: 'C:/R/Rscript.exe',
      kb_path: './knowledge_base',
      kb_ok: true,
      ollama: { state: 'ready', managed: false, detail: '' },
    },
    soft_warn: false,
  })
  fetchMock.mockImplementation(routeFetch)
  payloads.clear()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('ChatPage 输入区：键位 / 停止 / 错误提示（约束 3）', () => {
  it('打开页面自动选中首个会话，首次拉历史带 restore=1 并渲染历史消息', async () => {
    const sid = nextSid()
    await renderReady(sid, { messages: [makeMsg(sid, { role: 'user', content: '初始问题' })] })

    expect(useChatStore.getState().sessionId).toBe(sid)
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/sessions/${sid}/messages?restore=1`),
      undefined,
    )
    expect(screen.getByText('初始问题')).toBeInTheDocument()
  })

  it('Enter 发送（清空输入、进入生成态），Shift+Enter 与空输入不发送', async () => {
    const sid = nextSid()
    await renderReady(sid)
    const box = screen.getByPlaceholderText<HTMLTextAreaElement>('输入你的问题…')

    // Shift+Enter：处理器不拦截（交给浏览器默认换行），绝不触发发送
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true })
    expect(streamChatMock).not.toHaveBeenCalled()

    // 多行草稿原样作为 prompt
    fireEvent.change(box, { target: { value: '你好\n第二行' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(streamChatMock).toHaveBeenCalledTimes(1))
    expect(streamChatMock).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: sid, prompt: '你好\n第二行' }),
    )
    expect(box).toHaveValue('')
    expect(useChatStore.getState().busy).toBe(true)
    expect(screen.getByRole('button', { name: '停止' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()

    // 输入已清空：回车不再重复发送
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(streamChatMock).toHaveBeenCalledTimes(1)
  })

  it('Esc 中断：置位 abort 信号、显示诚实停止文案，busy 不被立刻关闭', async () => {
    const sid = nextSid()
    await renderReady(sid)
    let signal: AbortSignal | undefined
    streamChatMock.mockImplementation((options: { signal: AbortSignal }) => {
      signal = options.signal
      return new Promise<void>(() => {})
    })

    const box = screen.getByPlaceholderText('输入你的问题…')
    fireEvent.change(box, { target: { value: '中断我' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(signal).toBeDefined())
    expect(signal!.aborted).toBe(false)

    fireEvent.keyDown(document.body, { key: 'Escape' })

    expect(signal!.aborted).toBe(true)
    expect(screen.getByText(/正在结束当前步骤/)).toBeInTheDocument()
    // 约束 3：只置 abort，不承诺瞬时取消（busy 等 onEnd 收尾）
    expect(useChatStore.getState().busy).toBe(true)
  })

  it('生成中显示停止按钮；点击后显示诚实停止文案且 busy 不立刻关闭', async () => {
    const sid = nextSid()
    await renderReady(sid)
    act(() => {
      useChatStore.setState({ busy: true })
    })

    expect(screen.getByRole('button', { name: '停止' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    expect(screen.getByText('生成中…')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '停止' }))

    expect(screen.getByText(/已请求停止，正在结束当前步骤/)).toBeInTheDocument()
    expect(useChatStore.getState().busy).toBe(true)
  })

  it('发送遇 409：提示「该会话已有任务在进行」', async () => {
    const sid = nextSid()
    await renderReady(sid)
    streamChatMock.mockImplementation(() =>
      Promise.reject(new ApiError(409, '该会话已有任务在进行')),
    )

    const box = screen.getByPlaceholderText('输入你的问题…')
    fireEvent.change(box, { target: { value: '跑个分析' } })
    fireEvent.keyDown(box, { key: 'Enter' })

    await waitFor(() => expect(screen.getByText('该会话已有任务在进行')).toBeInTheDocument())
    expect(useChatStore.getState().busy).toBe(false)
  })

  it('超 30s 无 store 事件时提示「仍在执行，可能耗时较久」', async () => {
    const sid = nextSid()
    await renderReady(sid)
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] })

    act(() => {
      useChatStore.setState({ busy: true })
    })
    expect(screen.queryByText(/仍在执行，可能耗时较久/)).not.toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(30_000)
    })
    expect(screen.getByText(/仍在执行，可能耗时较久/)).toBeInTheDocument()
  })
})

describe('ChatPage 约束落点（1/2）', () => {
  it('约束1：空内容助手消息显示兜底，点重试用最后一条用户消息重发', async () => {
    const sid = nextSid()
    const user = makeMsg(sid, { role: 'user', content: '上次问题' })
    const empty = makeMsg(sid, { role: 'assistant', content: '' })
    await renderReady(sid, { messages: [user, empty] })

    expect(screen.getByText(EMPTY_FALLBACK)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() =>
      expect(streamChatMock).toHaveBeenCalledWith(
        expect.objectContaining({ sessionId: sid, prompt: '上次问题' }),
      ),
    )
  })

  it('约束2：过程条为 end 时输入区仍是生成态，不出现「已完成」', async () => {
    const sid = nextSid()
    await renderReady(sid)
    act(() => {
      useChatStore.setState({
        busy: true,
        tools: [{ name: 'run_analysis', phase: 'end', label: '已完成' }],
      })
    })

    expect(screen.getByRole('button', { name: '停止' })).toBeInTheDocument()
    expect(screen.getByText('生成中…')).toBeInTheDocument()
    expect(screen.queryByText(/已完成/)).not.toBeInTheDocument()
    expect(useChatStore.getState().busy).toBe(true)
  })

  it('soft_warn 恢复后只显示一条压缩提醒横幅（不在 ChatPage 重复渲染）', async () => {
    const sid = nextSid()
    await renderReady(sid, { extra: { soft_warn: true } })

    expect(await screen.findByText(/上下文接近压缩阈值/)).toBeInTheDocument()
    expect(screen.getAllByText(/上下文接近压缩阈值/)).toHaveLength(1)
  })

  it('历史消息的图表渲染进 charts-slot（ChartBlock 接线）', async () => {
    const sid = nextSid()
    const withChart = makeMsg(sid, {
      content: '',
      results: { charts: [{ type: 'image', image_b64: 'eHl6' }] },
    })
    await renderReady(sid, { messages: [withChart] })

    const slot = screen.getByTestId('charts-slot')
    expect(within(slot).getByTestId('chart-image')).toBeInTheDocument()
  })
})

describe('ChatPage 确认卡片接线', () => {
  it('?restore=1 恢复：pending 脚本按顶层 pending_script 转 expired 并挂回卡片', async () => {
    const sid = nextSid()
    const embedded: PendingScript = {
      script: 'x <- 1',
      analysis_type: 'bulk_de',
      params: {},
      method_context: null,
      user_request: '跑差异',
      status: 'pending',
    }
    const restored: PendingScript = { ...embedded, status: 'expired' }
    const holder = makeMsg(sid, { content: '请确认脚本', pending_script: embedded })
    await renderReady(sid, { messages: [holder], extra: { pending_script: restored } })

    expect(screen.getByTestId('script-card')).toHaveTextContent('已过期（会话已恢复，需重新发起）')
    expect(screen.queryByRole('button', { name: '执行脚本' })).not.toBeInTheDocument()
    expect(useChatStore.getState().scriptCard?.message_id).toBe(holder.id)
  })

  it('脚本确认成功：按 action 回写 status（confirmed）', async () => {
    const sid = nextSid()
    await renderReady(sid)
    act(() => {
      useChatStore.setState({ scriptCard: pendingCard() })
    })
    confirmActionMock.mockResolvedValue({ message: makeMsg(sid) })

    fireEvent.click(screen.getByRole('button', { name: '执行脚本' }))

    await waitFor(() =>
      expect(confirmActionMock).toHaveBeenCalledWith({ session_id: sid, action: 'script_confirm' }),
    )
    await waitFor(() => expect(useChatStore.getState().scriptCard?.status).toBe('confirmed'))
    expect(screen.getByText('已确认执行')).toBeInTheDocument()
  })

  it('脚本确认失败展示后端 detail，再次提交先清错误并可成功', async () => {
    const sid = nextSid()
    await renderReady(sid)
    act(() => {
      useChatStore.setState({ scriptCard: pendingCard() })
    })
    confirmActionMock.mockRejectedValueOnce(new ApiError(409, '该会话已有任务在进行'))
    confirmActionMock.mockResolvedValue({ message: makeMsg(sid) })

    fireEvent.click(screen.getByRole('button', { name: '执行脚本' }))
    await waitFor(() => expect(screen.getByText('该会话已有任务在进行')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: '执行脚本' }))
    await waitFor(() => expect(confirmActionMock).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(useChatStore.getState().scriptCard?.status).toBe('confirmed'))
    expect(screen.queryByText('该会话已有任务在进行')).not.toBeInTheDocument()
  })

  it('数据确认成功：调用 confirmAction 后清除数据卡', async () => {
    const sid = nextSid()
    await renderReady(sid)
    const dataCard: DataCardPayload = {
      message_id: 1,
      candidates: [
        {
          source: 'GEO',
          asset_id: 'GSE1',
          title: '数据集1',
          asset_type: 'series',
          reason: '相关',
          description: '',
          metadata: {},
        },
      ],
      query: 'GSE1',
      message: '找到 1 个候选',
    }
    act(() => {
      useChatStore.setState({ dataCard })
    })
    confirmActionMock.mockResolvedValue({ message: makeMsg(sid) })

    fireEvent.click(screen.getByRole('button', { name: '使用此数据集' }))

    await waitFor(() =>
      expect(confirmActionMock).toHaveBeenCalledWith({
        session_id: sid,
        action: 'data_confirm',
        source: 'GEO',
        asset_id: 'GSE1',
        query: 'GSE1',
      }),
    )
    await waitFor(() => expect(screen.queryByTestId('data-card')).not.toBeInTheDocument())
    expect(useChatStore.getState().dataCard).toBeNull()
  })

  it('重新生成：用 pending_script.user_request 再发一轮', async () => {
    const sid = nextSid()
    await renderReady(sid)
    act(() => {
      useChatStore.setState({ scriptCard: pendingCard({ user_request: '重新跑差异分析' }) })
    })

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }))

    await waitFor(() =>
      expect(streamChatMock).toHaveBeenCalledWith(
        expect.objectContaining({ prompt: '重新跑差异分析', sessionId: sid }),
      ),
    )
  })
})

describe('ChatPage 会话列表接线', () => {
  it('点击侧栏会话：切换活动会话并为新会话首次拉历史（带 restore=1）', async () => {
    const sidA = nextSid()
    const sidB = nextSid()
    await renderReady(sidA, {
      sessions: [makeSummary(sidA, '甲会话'), makeSummary(sidB, '乙会话')],
      messages: [makeMsg(sidA, { role: 'user', content: 'A 的问题' })],
    })

    fireEvent.click(screen.getByRole('button', { name: /^乙会话/ }))

    await waitFor(() => expect(useChatStore.getState().sessionId).toBe(sidB))
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/sessions/${sidB}/messages?restore=1`),
        undefined,
      ),
    )
  })

  it('新建会话：切到新会话并刷新会话列表', async () => {
    const sid = nextSid()
    await renderReady(sid)
    createSessionMock.mockResolvedValue({
      session: {
        id: 'fresh-1',
        title: '新会话',
        created_at: '2026-10-01T00:00:00Z',
        updated_at: '2026-10-01T00:00:00Z',
        summary: null,
        summary_upto: 0,
        downloaded_assets: [],
      },
    })

    fireEvent.click(screen.getByRole('button', { name: '新建会话' }))

    await waitFor(() => expect(useChatStore.getState().sessionId).toBe('fresh-1'))
    await waitFor(() => expect(listSessionsMock.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('删除当前会话：自动切到第一个剩余会话', async () => {
    const sidA = nextSid()
    const sidB = nextSid()
    deleteSessionMock.mockResolvedValue({ deleted: sidA })
    await renderReady(sidA, {
      sessions: [makeSummary(sidA, '甲会话'), makeSummary(sidB, '乙会话')],
    })

    fireEvent.click(screen.getByRole('button', { name: '删除 甲会话' }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))

    await waitFor(() => expect(useChatStore.getState().sessionId).toBe(sidB))
  })

  it('删光会话：清空当前会话状态', async () => {
    const sid = nextSid()
    deleteSessionMock.mockResolvedValue({ deleted: sid })
    await renderReady(sid)

    fireEvent.click(screen.getByRole('button', { name: '删除 测试会话' }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))

    await waitFor(() => expect(useChatStore.getState().sessionId).toBeNull())
    expect(useChatStore.getState().history).toEqual([])
  })
})
