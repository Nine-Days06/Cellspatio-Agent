import { beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
  vi.resetModules()
})

describe('rerank preference', () => {
  it('存储写入失败时当前页面仍使用内存值', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota exceeded')
    })
    const { readRerankPreference, writeRerankPreference } = await import('@/lib/preferences')

    writeRerankPreference(true)

    expect(readRerankPreference()).toBe(true)
  })
})
