/** 统一 HTTP 传输：JSON 请求与流式请求共用错误解析。 */

/** 统一 HTTP 错误：message 直接用后端 detail（已是中文），UI 可直接展示。 */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export const jsonHeaders = { 'Content-Type': 'application/json' } as const

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

/** fetch 并返回原始 Response；网络错误归一为 ApiError(0)，非 2xx 抛后端 detail。 */
export async function requestResponse(path: string, init?: RequestInit): Promise<Response> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : '网络请求失败')
  }
  if (!response.ok) throw await toApiError(response)
  return response
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await requestResponse(path, init)
  return (await response.json()) as T
}
