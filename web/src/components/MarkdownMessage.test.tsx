import { act, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MarkdownMessage } from './MarkdownMessage'

const loadContent = vi.hoisted(() => vi.fn())
vi.mock('./MarkdownContent', () => {
  loadContent()
  throw new Error('Chunk unavailable')
})

afterEach(() => vi.unstubAllGlobals())

it('视口外不加载富文本模块；下载失败仍显示最新原文而不执行 HTML', async () => {
  let enterViewport: IntersectionObserverCallback
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: IntersectionObserverCallback) { enterViewport = callback }
    observe() {}
    disconnect() {}
  })
  const { rerender } = render(<MarkdownMessage text={'## 标题\n<script>坏内容</script>'} restrictExternalContent />)
  expect(loadContent).not.toHaveBeenCalled()
  expect(screen.getByText('## 标题')).toBeInTheDocument()
  expect(document.querySelector('script')).toBeNull()

  act(() => enterViewport([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver))
  expect(await screen.findByRole('status')).toHaveTextContent('仍可阅读原文')
  expect(loadContent).toHaveBeenCalledTimes(1)
  rerender(<MarkdownMessage text="更新的原文" restrictExternalContent />)
  expect(screen.getByText('更新的原文')).toBeInTheDocument()
})
