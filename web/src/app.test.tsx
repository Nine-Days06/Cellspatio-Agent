import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import App from '@/App'

afterEach(() => {
  window.history.pushState({}, '', '/')
})

describe('App 页面容器', () => {
  it('首页在动效容器内，标题保持可访问名称', () => {
    window.history.pushState({}, '', '/')
    render(<App />)

    expect(
      screen.getByRole('heading', { name: '从单细胞数据到可复核的分析记录' }),
    ).toBeInTheDocument()
    expect(document.querySelector('.motion-page')).toContainElement(
      screen.getByRole('heading', { name: '从单细胞数据到可复核的分析记录' }),
    )
  })

  it('登录页在动效容器内，标题保持可访问名称', () => {
    window.history.pushState({}, '', '/login')
    render(<App />)

    expect(screen.getByRole('heading', { name: '登录管理账户' })).toBeInTheDocument()
    expect(document.querySelector('.motion-page')).toContainElement(
      screen.getByRole('heading', { name: '登录管理账户' }),
    )
  })
})
