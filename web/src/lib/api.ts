import type {
  ConfirmRequestBody,
  MessagesResponse,
  Session,
  SessionSummary,
  SidebarResponse,
  WireMessage,
} from '@/lib/types'

/** 统一 HTTP 错误：message 直接用后端 detail（已是中文），UI 可直接展示。 */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let detail = ''
  try {
    const body = (await response.json()) as { detail?: string }
    detail = body.detail ?? ''
  } catch {
    detail = ''
  }
  return new ApiError(response.status, detail || `请求失败（HTTP ${response.status}）`)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : '网络请求失败')
  }
  if (!response.ok) throw await toApiError(response)
  return (await response.json()) as T
}

const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
})

export function listSessions(): Promise<{ sessions: SessionSummary[] }> {
  return request('/api/sessions', { method: 'GET' })
}

export function createSession(title?: string): Promise<{ session: Session }> {
  return request('/api/sessions', jsonInit('POST', title ? { title } : {}))
}

export function deleteSession(sessionId: string): Promise<{ deleted: string }> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}`, jsonInit('DELETE'))
}

/**
 * 每会话只在**首次**拉历史带 restore=1：后端 restore 会就地把最新 pending 改成
 * expired（`_restore_pending`），TanStack Query 的 refetch（窗口聚焦/失效重拉）
 * 再带 restore 会误杀用户尚未处理的新 pending。
 * 标记只在请求**成功后**进行：失败的首次请求若提前标记，重试将永久丢失
 * restore=1，旧 pending 卡片不会转 expired，可能被误点确认执行。
 */
const restoredSessions = new Set<string>()

export async function fetchMessages(sessionId: string): Promise<MessagesResponse> {
  const restore = restoredSessions.has(sessionId) ? '' : '?restore=1'
  const body = await request<MessagesResponse>(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages${restore}`,
  )
  restoredSessions.add(sessionId)
  return body
}

export function fetchSidebar(sessionId?: string): Promise<SidebarResponse> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return request(`/api/sidebar${query}`)
}

export function confirmAction(body: ConfirmRequestBody): Promise<{ message: WireMessage }> {
  return request('/api/confirm', jsonInit('POST', body))
}
