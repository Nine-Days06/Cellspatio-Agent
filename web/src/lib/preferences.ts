/** 浏览器本地偏好；存储不可用时回落默认值，不影响当前页面。 */

export const RERANK_KEY = 'cellspatio-rerank'

let fallback: boolean | null = null
let storageBroken = false

export function readRerankPreference(): boolean {
  if (storageBroken && fallback !== null) return fallback
  try {
    fallback = localStorage.getItem(RERANK_KEY) === '1'
    return fallback
  } catch {
    storageBroken = true
    return fallback ?? false
  }
}

export function writeRerankPreference(enabled: boolean): void {
  fallback = enabled
  try {
    localStorage.setItem(RERANK_KEY, enabled ? '1' : '0')
    storageBroken = false
  } catch {
    // 浏览器禁用存储时，仅当前页面生效。
    storageBroken = true
  }
}
