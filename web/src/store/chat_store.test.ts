import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import { streamChat, type EndReason, type SseEvent } from '@/lib/sse'
import { useChatStore } from '@/store/chat'
import type { DataCardPayload, ScriptCardPayload, WireMessage } from '@/lib/types'

// 只替换 streamChat（send 依赖它），其余 sse 导出保持真实实现
vi.mock('@/lib/sse', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/sse')>()),
  streamChat: vi.fn(),
}))

function assistant(overrides: Partial<WireMessage> = {}): WireMessage {
  return {
    id: 1,
    session_id: 's1',
    seq: 1,
    role: 'assistant',
    content: '你好',
    timestamp: '2026-09-30T00:00:00',
    results: null,
    pending_script: null,
    ...overrides,
  }
}

/** 拦下一次 streamChat：回调/信号交给测试手动驱动，release 放行挂起的 send。 */
interface StreamCtl {
  onEvent?: (event: SseEvent) => void
  onEnd?: (reason: EndReason) => void
  signal?: AbortSignal
}

function holdStream(): { ctl: StreamCtl; release: () => void } {
  const ctl: StreamCtl = {}
  let release = () => {}
  const gate = new Promise<void>((resolve) => {
    release = () => resolve(undefined)
  })
  vi.mocked(streamChat).mockImplementationOnce((options) => {
    ctl.onEvent = options.onEvent
    ctl.onEnd = options.onEnd
    ctl.signal = options.signal
    return gate
  })
  return { ctl, release: () => release() }
}

beforeEach(() => {
  useChatStore.setState(useChatStore.getInitialState(), true)
  vi.mocked(streamChat).mockReset()
})

describe('turn 生命周期', () => {
  it('beginTurn 置 busy=true、清空草稿并追加用户消息', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('跑差异表达')

    const s = useChatStore.getState()
    expect(s.busy).toBe(true)
    expect(s.draft).toBe('')
    expect(s.history.at(-1)).toMatchObject({ role: 'user', content: '跑差异表达' })
  })

  it('appendDelta 累积流式草稿', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')
    store.appendDelta('正在')
    store.appendDelta('处理')

    expect(useChatStore.getState().draft).toBe('正在处理')
  })

  it('约束2：只收到 tool_status start 不足以关闭 busy', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')
    store.pushTool({ name: 'run_analysis', phase: 'start', label: '运行分析' })
    store.appendDelta('中间文本')
    store.pushTool({ name: 'run_analysis', phase: 'end', label: '运行分析' })

    expect(useChatStore.getState().busy).toBe(true)
  })

  it('endTurn 才关闭 busy，并清空过程条', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')
    store.pushTool({ name: 'run_analysis', phase: 'start', label: '运行分析' })

    useChatStore.getState().endTurn('done')

    const s = useChatStore.getState()
    expect(s.busy).toBe(false)
    expect(s.tools).toHaveLength(0)
    expect(s.draft).toBe('')
  })

  it('failTurn 关闭 busy 并写入错误提示', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')

    useChatStore.getState().failTurn('LLM 调用失败')

    const s = useChatStore.getState()
    expect(s.busy).toBe(false)
    expect(s.notice).toEqual({ kind: 'error', text: 'LLM 调用失败' })
  })

  it('收尾幂等：endTurn 之后再 endTurn 不改变状态', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')
    useChatStore.getState().endTurn('done')
    useChatStore.getState().failTurn('不该出现')

    expect(useChatStore.getState().notice).toBeNull()
  })

  it('aborted 收尾后写入诚实提示（由 ChatPage 覆盖文案亦可）', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')

    useChatStore.getState().endTurn('aborted')

    expect(useChatStore.getState().busy).toBe(false)
  })
})

describe('finalizeTurn', () => {
  it('done 消息落 history 并清空草稿', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')
    store.appendDelta('部分文本')

    store.finalizeTurn(assistant({ id: 7, content: '完整回答' }))

    const s = useChatStore.getState()
    expect(s.draft).toBe('')
    expect(s.history.at(-1)).toMatchObject({ id: 7, content: '完整回答' })
  })

  it('done 消息无图表时带回流式 chart 事件累积的图表', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')
    store.pushChart({ type: 'image', image_b64: 'eHl6' })

    store.finalizeTurn(assistant({ results: { charts: [] } }))

    expect(useChatStore.getState().history.at(-1)?.results?.charts).toEqual([
      { type: 'image', image_b64: 'eHl6' },
    ])
  })

  it('done 消息自带图表时优先用消息里的（不重复）', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')
    store.pushChart({ type: 'image', image_b64: '旧的' })

    store.finalizeTurn(
      assistant({
        results: { charts: [{ type: 'image', image_b64: '新的' }] },
      }),
    )

    expect(useChatStore.getState().history.at(-1)?.results?.charts).toEqual([
      { type: 'image', image_b64: '新的' },
    ])
  })
})

describe('卡片状态', () => {
  const scriptCard: ScriptCardPayload = {
    message_id: 3,
    script: 'DESeq2 代码',
    analysis_type: 'bulk_de',
    params: {},
    method_context: null,
    user_request: '跑差异表达',
    status: 'pending',
  }

  const dataCard: DataCardPayload = {
    message_id: 4,
    query: '肝癌',
    message: '找到 2 个候选',
    candidates: [
      {
        source: 'geo',
        asset_id: 'GSE1',
        title: 'HCC RNA-seq',
        asset_type: 'dataset',
        reason: '匹配关键词',
        description: '',
        metadata: {},
      },
    ],
  }

  it('脚本卡片在 done 收尾后仍保留（done.message 不含卡片状态）', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')
    store.setScriptCard(scriptCard)

    store.finalizeTurn(assistant())
    store.endTurn('done')

    expect(useChatStore.getState().scriptCard).toEqual(scriptCard)
  })

  it('数据候选卡片在收尾后仍保留，下一轮 beginTurn 才清空', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')
    store.setDataCard(dataCard)
    store.finalizeTurn(assistant())
    store.endTurn('done')

    expect(useChatStore.getState().dataCard).toEqual(dataCard)

    useChatStore.getState().beginTurn('下一轮')
    expect(useChatStore.getState().dataCard).toBeNull()
    expect(useChatStore.getState().scriptCard).toBeNull()
    expect(useChatStore.getState().notice).toBeNull()
  })
})

describe('约束1：空内容照常记录', () => {
  it('done 空内容也落 history，不丢弃、不写错误提示', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')

    store.finalizeTurn(assistant({ id: 9, content: '' }))
    store.endTurn('done')

    const s = useChatStore.getState()
    expect(s.history.at(-1)).toMatchObject({ role: 'assistant', content: '' })
    expect(s.notice).toBeNull()
    expect(s.busy).toBe(false)
  })

  it('空内容但有流式图表 → 图表随消息保留（组件层据此抑制兜底）', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')
    store.pushChart({ type: 'image', image_b64: 'eHl6' })

    store.finalizeTurn(assistant({ content: '' }))

    expect(useChatStore.getState().history.at(-1)?.results?.charts).toEqual([
      { type: 'image', image_b64: 'eHl6' },
    ])
  })
})

describe('约束2：收尾唯一通道（动作层）', () => {
  it('tool_status end 不关 busy；endTurn / failTurn 才关', () => {
    const store = useChatStore.getState()
    store.setSession('s1', [])
    store.beginTurn('hi')
    store.pushTool({ name: 'run_analysis', phase: 'end', label: '运行分析' })
    expect(useChatStore.getState().busy).toBe(true)

    useChatStore.getState().endTurn('done')
    expect(useChatStore.getState().busy).toBe(false)

    store.beginTurn('第二轮')
    store.pushTool({ name: 'run_analysis', phase: 'end', label: '运行分析' })
    expect(useChatStore.getState().busy).toBe(true)

    useChatStore.getState().failTurn('boom')
    expect(useChatStore.getState().busy).toBe(false)
  })
})

describe('send：SSE 接线', () => {
  it('约束2（流式层）：tool_status/delta 不关 busy，onEnd 才关', async () => {
    const { ctl, release } = holdStream()
    useChatStore.getState().setSession('s1', [])
    const pending = useChatStore.getState().send('hi')
    expect(useChatStore.getState().busy).toBe(true)

    ctl.onEvent?.({ type: 'tool_status', name: 'run_analysis', phase: 'start', label: '运行分析' })
    ctl.onEvent?.({ type: 'delta', text: '中间文本' })
    ctl.onEvent?.({ type: 'tool_status', name: 'run_analysis', phase: 'end', label: '运行分析' })
    expect(useChatStore.getState().busy).toBe(true)

    ctl.onEnd?.('closed')
    expect(useChatStore.getState().busy).toBe(false)

    release()
    await pending
  })

  it('happy path：delta→draft、done→history、onEnd(done) 收尾', async () => {
    const { ctl, release } = holdStream()
    useChatStore.getState().setSession('s1', [])
    const pending = useChatStore.getState().send('跑差异表达')

    expect(streamChat).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 's1', prompt: '跑差异表达' }),
    )

    ctl.onEvent?.({ type: 'delta', text: '正在' })
    ctl.onEvent?.({ type: 'delta', text: '处理' })
    expect(useChatStore.getState().draft).toBe('正在处理')

    ctl.onEvent?.({
      type: 'done',
      message_id: 7,
      message: assistant({ id: 7, content: '完整回答' }),
    })
    expect(useChatStore.getState().history.at(-1)).toMatchObject({
      role: 'assistant',
      content: '完整回答',
    })

    ctl.onEnd?.('done')
    release()
    await pending

    const s = useChatStore.getState()
    expect(s.busy).toBe(false)
    expect(s.draft).toBe('')
    expect(s.notice).toBeNull()
  })

  it('error 事件 → failTurn 关 busy 并写提示；后续 onEnd(error) 不覆盖', async () => {
    const { ctl, release } = holdStream()
    useChatStore.getState().setSession('s1', [])
    const pending = useChatStore.getState().send('hi')

    ctl.onEvent?.({ type: 'error', message: 'LLM 调用失败' })
    expect(useChatStore.getState().busy).toBe(false)
    expect(useChatStore.getState().notice).toEqual({ kind: 'error', text: 'LLM 调用失败' })

    ctl.onEnd?.('error')
    expect(useChatStore.getState().notice).toEqual({ kind: 'error', text: 'LLM 调用失败' })

    release()
    await pending
  })

  it('HTTP 409 → ApiError detail 落提示（onEnd(error) 未提前收尾时由 catch 兜）', async () => {
    vi.mocked(streamChat).mockImplementationOnce((options) => {
      options.onEnd('error')
      return Promise.reject(new ApiError(409, '该会话已有任务在进行'))
    })
    useChatStore.getState().setSession('s1', [])

    await useChatStore.getState().send('并发提问')

    const s = useChatStore.getState()
    expect(s.busy).toBe(false)
    expect(s.notice).toEqual({ kind: 'error', text: '该会话已有任务在进行' })
  })

  it('stop() 只中止信号，busy 等 onEnd(aborted) 才关', async () => {
    const { ctl, release } = holdStream()
    useChatStore.getState().setSession('s1', [])
    const pending = useChatStore.getState().send('hi')

    useChatStore.getState().stop()
    expect(ctl.signal?.aborted).toBe(true)
    expect(useChatStore.getState().busy).toBe(true)

    ctl.onEnd?.('aborted')
    expect(useChatStore.getState().busy).toBe(false)

    release()
    await pending
  })

  it('分发接线：compressed→softWarn、chart→figure_json、confirm_card→脚本卡片', async () => {
    const scriptCard: ScriptCardPayload = {
      message_id: 3,
      script: 'DESeq2 代码',
      analysis_type: 'bulk_de',
      params: {},
      method_context: null,
      user_request: '跑差异表达',
      status: 'pending',
    }
    const { ctl, release } = holdStream()
    useChatStore.getState().setSession('s1', [])
    const pending = useChatStore.getState().send('跑差异表达')

    ctl.onEvent?.({ type: 'compressed', soft_warn: true })
    expect(useChatStore.getState().softWarn).toBe(true)

    ctl.onEvent?.({ type: 'chart', plotly_json: '{"data":[]}' })
    ctl.onEvent?.({ type: 'confirm_card', kind: 'script', payload: scriptCard })
    expect(useChatStore.getState().scriptCard).toEqual(scriptCard)

    ctl.onEvent?.({ type: 'done', message_id: 3, message: assistant({ results: { charts: [] } }) })
    expect(useChatStore.getState().history.at(-1)?.results?.charts).toEqual([
      { type: 'plotly', figure_json: '{"data":[]}' },
    ])

    ctl.onEnd?.('done')
    release()
    await pending
    expect(useChatStore.getState().softWarn).toBe(true)
  })

  it('softWarn 会话级：beginTurn 保留，setSession 复位', async () => {
    const { ctl, release } = holdStream()
    useChatStore.getState().setSession('s1', [])
    const pending = useChatStore.getState().send('hi')

    ctl.onEvent?.({ type: 'compressed', soft_warn: true })
    ctl.onEnd?.('done')
    release()
    await pending
    expect(useChatStore.getState().softWarn).toBe(true)

    useChatStore.getState().beginTurn('下一轮')
    expect(useChatStore.getState().softWarn).toBe(true)

    useChatStore.getState().setSession('s2', [])
    expect(useChatStore.getState().softWarn).toBe(false)
  })
})
