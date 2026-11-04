import html from '../../index.html?raw'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeToggle } from '@/components/ThemeToggle'

const headScript = html.match(/<script>([\s\S]*?)<\/script>/)![1]

function restoreTheme(): void {
  new Function(headScript)()
}

beforeEach(() => {
  document.documentElement.classList.remove('dark')
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  document.documentElement.classList.remove('dark')
  localStorage.clear()
})

describe('ThemeToggle', () => {
  it('默认浅色，双向切换并记住选择', () => {
    restoreTheme()
    render(<ThemeToggle />)
    fireEvent.click(screen.getByRole('button', { name: '切换到深色模式' }))
    expect(document.documentElement).toHaveClass('dark')
    expect(localStorage.getItem('cellspatio-theme')).toBe('dark')

    fireEvent.click(screen.getByRole('button', { name: '切换到浅色模式' }))
    expect(document.documentElement).not.toHaveClass('dark')
    expect(localStorage.getItem('cellspatio-theme')).toBe('light')
  })

  it('首屏脚本恢复保存的深色，按钮与页面主题一致', () => {
    localStorage.setItem('cellspatio-theme', 'dark')
    restoreTheme()
    expect(document.documentElement).toHaveClass('dark')
    render(<ThemeToggle />)
    expect(screen.getByRole('button', { name: '切换到浅色模式' })).toBeInTheDocument()
  })

  it('已保存浅色时不会继承旧的深色类', () => {
    document.documentElement.classList.add('dark')
    localStorage.setItem('cellspatio-theme', 'light')
    restoreTheme()
    expect(document.documentElement).not.toHaveClass('dark')
  })

  it('存储不可用时仍能加载并切换当前页面', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('存储不可用') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('存储不可用') })
    expect(restoreTheme).not.toThrow()
    render(<ThemeToggle />)
    fireEvent.click(screen.getByRole('button', { name: '切换到深色模式' }))
    expect(document.documentElement).toHaveClass('dark')
    expect(screen.getByRole('button', { name: '切换到浅色模式' })).toBeInTheDocument()
  })
})
