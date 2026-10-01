import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, streamChat, type SseEvent } from '@/lib/sse'

const encoder = new TextEncoder()

function frame(event: Record<string, unknown>): string {
  return `data: ${JSON.stringify(event)}\n\n`
}

function sseResponse(chunks: (string | Uint8Array)[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk)
      }
      controller.close()
    },
  })
  return new Response(body, { status: 200 })
}

function jsonResponse(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

interface Capture {
  events: SseEvent[]
  ends: string[]
}

function capture(): Capture {
  return { events: [], ends: [] }
}

function options(c: Capture, signal?: AbortSignal) {
  return {
    sessionId: 's1',
    prompt: '你好',
    onEvent: (event: SseEvent) => c.events.push(event),
    onEnd: (reason: string) => c.ends.push(reason),
    signal,
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('streamChat 帧解析', () => {
  it('连续多帧按顺序分发，中文不乱码', async () => {
    const c = capture()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse([
        frame({ type: 'delta', text: '你好，' }),
        frame({ type: 'delta', text: '世界' }),
        frame({
          type: 'tool_status',
          name: 'query_knowledge',
          phase: 'start',
          label: '查询知识库',
        }),
        frame({ type: 'done', message_id: 7 }),
      ]),
    )

    await streamChat(options(c))

    expect(c.events.map((e) => e.type)).toEqual([
      'delta',
      'delta',
      'tool_status',
      'done',
    ])
    expect(c.events[0].text).toBe('你好，')
    expect(c.events[2].label).toBe('查询知识库')
    expect(c.ends).toEqual(['done'])
  })

  it('帧被拆成半包（chunk 中间断开）仍能解析', async () => {
    const c = capture()
    const whole = frame({ type: 'delta', text: '半个包' })
    const cut = Math.floor(whole.length / 2)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse([
        whole.slice(0, cut),
        whole.slice(cut),
        frame({ type: 'done', message_id: 1 }),
      ]),
    )

    await streamChat(options(c))

    expect(c.events).toHaveLength(2)
    expect(c.events[0].text).toBe('半个包')
    expect(c.ends).toEqual(['done'])
  })

  it('多字节 UTF-8 字符被切断两半仍完整还原', async () => {
    const c = capture()
    const single = frame({ type: 'delta', text: '单细胞聚类注释流程' })
    const bytes = encoder.encode(single)
    const cut = 30 // 落在某个中文字符的字节中间
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse([bytes.slice(0, cut), bytes.slice(cut)]),
    )

    await streamChat(options(c))

    expect(c.events).toHaveLength(1)
    expect(c.events[0].text).toBe('单细胞聚类注释流程')
    expect(c.ends).toEqual(['closed'])
  })

  it('最后一帧没有结尾空行也能解析（连接关闭兜底）', async () => {
    const c = capture()
    const raw = `data: ${JSON.stringify({ type: 'delta', text: '尾帧' })}` // 无 \n\n
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(sseResponse([raw]))

    await streamChat(options(c))

    expect(c.events).toHaveLength(1)
    expect(c.events[0].text).toBe('尾帧')
    expect(c.ends).toEqual(['closed'])
  })

  it('没有 done/error 而连接关闭时，onEnd 收尾为 closed', async () => {
    const c = capture()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse([frame({ type: 'delta', text: '只有一半' })]),
    )

    await streamChat(options(c))

    expect(c.ends).toEqual(['closed'])
  })

  it('只收到 tool_status 也不关闭为 done，onEnd 收尾为 closed（约束 2）', async () => {
    const c = capture()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse([
        frame({ type: 'tool_status', name: 'run_analysis', phase: 'start', label: '运行分析' }),
        frame({ type: 'tool_status', name: 'run_analysis', phase: 'end', label: '运行分析' }),
      ]),
    )

    await streamChat(options(c))

    // 后端不保证发 tool_status end；busy 只能由 done/error（或流结束兜底）关闭，
    // tool_status 只能用来渲染过程条文案，绝不能被当成收尾成功信号。
    expect(c.events).toHaveLength(2)
    expect(c.ends).toEqual(['closed'])
    expect(c.ends).not.toContain('done')
  })

  it('以 error 事件收尾时 onEnd 为 error', async () => {
    const c = capture()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse([frame({ type: 'error', message: 'LLM 调用失败', retryable: true })]),
    )

    await streamChat(options(c))

    expect(c.ends).toEqual(['error'])
  })
})

describe('streamChat 错误分支', () => {
  it('409 抛 ApiError 并带后端 detail', async () => {
    const c = capture()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(409, '该会话已有任务在进行'),
    )

    await expect(streamChat(options(c))).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      message: '该会话已有任务在进行',
    })
    expect(c.ends).toEqual(['error'])
  })

  it('404 抛 ApiError', async () => {
    const c = capture()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(404, '会话不存在'))

    await expect(streamChat(options(c))).rejects.toBeInstanceOf(ApiError)
    expect(c.ends).toEqual(['error'])
  })

  it('已中止的 signal 走 aborted 收尾且不抛错', async () => {
    const c = capture()
    const controller = new AbortController()
    controller.abort()
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(
      new DOMException('The operation was aborted.', 'AbortError'),
    )

    await streamChat(options(c, controller.signal))

    expect(c.ends).toEqual(['aborted'])
    expect(c.events).toHaveLength(0)
  })

  it('onEnd 只会被调用一次', async () => {
    const c = capture()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse([frame({ type: 'done', message_id: 1 })]),
    )

    await streamChat(options(c))

    expect(c.ends).toHaveLength(1)
  })
})

describe('ApiError 身份', () => {
  it('sse re-export 的 ApiError 与 api.ts 的是同一个类', async () => {
    const { ApiError: ApiErrorFromApi } = await import('@/lib/api')
    expect(ApiError).toBe(ApiErrorFromApi)
  })
})
