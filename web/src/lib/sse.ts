/** SSE 客户端：POST /api/chat 的 fetch 流解析 + 事件分发 + AbortController。

后端契约（src/api/events.py + routes_chat.py）：
- 请求：POST /api/chat，body {"session_id": "...", "prompt": "...", "mode": "chat"|"knowledge_qa"}
- 响应：text/event-stream，每帧一行 `data: {json}\n\n`
- 事件 7 类：delta / tool_status / confirm_card / chart / compressed / done / error
- delta 可带 source=reasoning，表示模型思考增量，不得当作正文草稿
- 错误：404 / 409 / 422，body 均为 {"detail": "..."}
- 不能用 EventSource：它只支持 GET，无法带 POST body
*/

export type SseEventType =
  | 'delta'
  | 'tool_status'
  | 'confirm_card'
  | 'chart'
  | 'compressed'
  | 'done'
  | 'error'

export interface SseEvent {
  type: SseEventType
  [key: string]: unknown
}

/** 流结束原因：done/error 由事件收尾，aborted = 本地取消，closed = 连接被对端关闭 */
export type EndReason = 'done' | 'error' | 'aborted' | 'closed'

// ApiError 单一实现住在 http.ts：sse 只 re-export，避免两个同名类导致 instanceof 判断分裂。
import { ApiError, jsonHeaders, requestResponse } from '@/lib/http'
import type { ChatMode } from '@/lib/types'
export { ApiError }

export interface StreamChatOptions {
  sessionId: string
  prompt: string
  mode?: ChatMode
  onEvent: (event: SseEvent) => void
  onEnd: (reason: EndReason) => void
  signal?: AbortSignal
}

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

/**
 * 发起一轮对话并逐帧分发事件。
 *
 * 结束语义（约束 2）：后端不保证发 `tool_status end`（agent_runtime 在工具返回
 * 终态时提前 return），busy 态只能由 `done` / `error` 事件或本函数的 onEnd 收尾
 * 关闭——onEnd 一定会被调用且只调用一次；tool_status 只用于渲染过程条文案。
 */
export async function streamChat(options: StreamChatOptions): Promise<void> {
  const { sessionId, prompt, mode = 'chat', onEvent, onEnd, signal } = options
  let ended = false
  const finish = (reason: EndReason) => {
    if (ended) return
    ended = true
    onEnd(reason)
  }

  let response: Response
  try {
    response = await requestResponse('/api/chat', {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify({ session_id: sessionId, prompt, mode }),
      signal,
    })
  } catch (err) {
    if (signal?.aborted || isAbortError(err)) {
      finish('aborted')
      return
    }
    // requestResponse 已把非 2xx 转成 ApiError；status>0 表示后端 HTTP 错误
    finish(err instanceof ApiError && err.status > 0 ? 'error' : 'closed')
    throw err
  }
  if (!response.body) {
    finish('closed')
    throw new ApiError(0, '响应无正文，无法读取事件流')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let lastEvent: SseEventType | null = null
  // 坏帧与正常 EOF 必须区分：末尾 done 被截断时，后端其实已落库，重试会重复提问。
  let parseError = false

  // 解析一段完整文本里的所有帧；坏 JSON 不中断整条流，但会标记解析失败
  const dispatch = (raw: string) => {
    for (const line of raw.split('\n')) {
      if (!line.startsWith('data: ')) continue
      const payload = line.slice('data: '.length)
      let parsed: SseEvent
      try {
        parsed = JSON.parse(payload) as SseEvent
      } catch {
        parseError = true
        continue
      }
      lastEvent = parsed.type
      onEvent(parsed)
    }
  }

  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      // stream: true 让跨 chunk 的多字节 UTF-8 字符先在解码器内缓冲
      buffer += decoder.decode(value, { stream: true })
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        dispatch(buffer.slice(0, boundary))
        buffer = buffer.slice(boundary + 2)
        boundary = buffer.indexOf('\n\n')
      }
    }
    buffer += decoder.decode()
    // 收尾 flush：最后一帧可能没有结尾空行
    if (buffer.trim()) dispatch(buffer)
  } catch (err) {
    if (isAbortError(err)) {
      finish('aborted')
      return
    }
    finish('closed')
    throw err
  }

  if (lastEvent === 'done') finish('done')
  else if (lastEvent === 'error') finish('error')
  else if (parseError) {
    finish('error')
    throw new ApiError(0, '响应解析失败，请刷新会话后重试。')
  }
  else finish('closed')
}
