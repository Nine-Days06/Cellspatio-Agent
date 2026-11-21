import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, jsonHeaders, requestJson, requestResponse } from '@/lib/http'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('requestJson', () => {
  it('2xx 解析 JSON body', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(200, { sessions: [] }))
    await expect(requestJson('/api/sessions')).resolves.toEqual({ sessions: [] })
  })

  it('非 2xx 使用后端 detail 作为 ApiError 文案', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(409, { detail: '该会话已有任务在进行' }),
    )
    await expect(requestJson('/api/sessions')).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      message: '该会话已有任务在进行',
    })
  })

  it('非 2xx 且 body 不是 JSON 时回退到 HTTP 状态文案', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('<html>bad gateway</html>', { status: 502 }),
    )
    await expect(requestJson('/api/sessions')).rejects.toMatchObject({
      status: 502,
      message: '请求失败（HTTP 502）',
    })
  })

  it('网络错误归一为 ApiError(status=0)', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(requestJson('/api/sessions')).rejects.toMatchObject({
      name: 'ApiError',
      status: 0,
      message: 'Failed to fetch',
    })
  })

  it('非 Error 的网络异常使用兜底文案', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue('boom')
    await expect(requestJson('/api/sessions')).rejects.toMatchObject({
      status: 0,
      message: '网络请求失败',
    })
  })
})

describe('requestResponse', () => {
  it('2xx 原样返回 Response，不消费 body', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(200, { ok: true }))
    const response = await requestResponse('/api/chat', {
      method: 'POST',
      headers: jsonHeaders,
    })
    expect(response.status).toBe(200)
    await expect(response.json()).resolves.toEqual({ ok: true })
  })

  it('非 2xx 抛出 ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(404, { detail: '会话不存在' }))
    await expect(requestResponse('/api/chat')).rejects.toBeInstanceOf(ApiError)
  })
})
