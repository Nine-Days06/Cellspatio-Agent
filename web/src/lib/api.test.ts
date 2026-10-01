import { beforeEach, describe, expect, it, vi } from 'vitest'
import { confirmAction, createSession, deleteSession, fetchMessages, fetchSidebar, listSessions } from '@/lib/api'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let lastRequest: { url: string; init: RequestInit | undefined } | null = null

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    lastRequest = { url: String(input), init }
    return handler(String(input), init)
  })
}

beforeEach(() => {
  lastRequest = null
  vi.restoreAllMocks()
})

describe('请求形状', () => {
  it('listSessions → GET /api/sessions', async () => {
    mockFetch(() => jsonResponse(200, { sessions: [] }))
    await expect(listSessions()).resolves.toEqual({ sessions: [] })
    expect(lastRequest?.url).toBe('/api/sessions')
    expect(lastRequest?.init?.method).toBe('GET')
  })

  it('createSession 带 title 时 POST body 为 {title}', async () => {
    mockFetch(() => jsonResponse(200, { session: { id: 's1' } }))
    await createSession('肝癌项目')
    expect(lastRequest?.url).toBe('/api/sessions')
    expect(JSON.parse(String(lastRequest?.init?.body))).toEqual({ title: '肝癌项目' })
  })

  it('fetchMessages 首次带 restore=1，同会话后续不带（restore 会就地过期 pending，refetch 不能重复带）', async () => {
    mockFetch(() =>
      jsonResponse(200, {
        session: { id: 's-restore' },
        messages: [],
        pending_script: null,
        soft_warn: false,
      }),
    )
    await fetchMessages('s-restore')
    expect(lastRequest?.url).toBe('/api/sessions/s-restore/messages?restore=1')
    const body = await fetchMessages('s-restore')
    expect(lastRequest?.url).toBe('/api/sessions/s-restore/messages')
    expect(body.soft_warn).toBe(false)
  })

  it('fetchSidebar 支持可选 session_id', async () => {
    mockFetch(() =>
      jsonResponse(200, {
        kb_stats: { initialized: true },
        env: { rscript: null, kb_path: './knowledge_base', kb_ok: true, ollama: { state: 'ready', managed: false, detail: '' } },
        soft_warn: false,
      }),
    )
    await fetchSidebar('s1')
    expect(lastRequest?.url).toBe('/api/sidebar?session_id=s1')
  })

  it('confirmAction 按 action 组装 body（script_confirm 不带 source/asset_id）', async () => {
    mockFetch(() => jsonResponse(200, { message: { id: 1 } }))
    await confirmAction({ session_id: 's1', action: 'script_confirm' })
    expect(lastRequest?.url).toBe('/api/confirm')
    expect(JSON.parse(String(lastRequest?.init?.body))).toEqual({
      session_id: 's1',
      action: 'script_confirm',
    })
  })

  it('confirmAction 的 data_confirm 带 source/asset_id/query', async () => {
    mockFetch(() => jsonResponse(200, { message: { id: 1 } }))
    await confirmAction({
      session_id: 's1',
      action: 'data_confirm',
      source: 'geo',
      asset_id: 'GSE1',
      query: '肝癌',
    })
    expect(JSON.parse(String(lastRequest?.init?.body))).toEqual({
      session_id: 's1',
      action: 'data_confirm',
      source: 'geo',
      asset_id: 'GSE1',
      query: '肝癌',
    })
  })
})

describe('错误处理', () => {
  it('409 → ApiError(status=409, message=后端 detail)', async () => {
    mockFetch(() => jsonResponse(409, { detail: '该会话已有任务在进行' }))
    await expect(deleteSession('s1')).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      message: '该会话已有任务在进行',
    })
  })

  it('404 → ApiError(status=404)', async () => {
    mockFetch(() => jsonResponse(404, { detail: '会话不存在' }))
    await expect(fetchMessages('missing')).rejects.toMatchObject({
      status: 404,
      message: '会话不存在',
    })
  })

  it('400 → ApiError(status=400)，保留后端动作错误文案', async () => {
    mockFetch(() => jsonResponse(400, { detail: '缺少 source 或 asset_id' }))
    await expect(
      confirmAction({ session_id: 's1', action: 'data_confirm' }),
    ).rejects.toMatchObject({ status: 400, message: '缺少 source 或 asset_id' })
  })

  it('500 兜底 JSON 的 detail 也能透传', async () => {
    mockFetch(() => jsonResponse(500, { detail: '意外错误' }))
    await expect(listSessions()).rejects.toMatchObject({
      status: 500,
      message: '意外错误',
    })
  })
})
