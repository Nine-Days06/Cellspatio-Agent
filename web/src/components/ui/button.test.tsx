import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Button } from '@/components/ui/button'

describe('Button', () => {
  it('渲染默认变体文案', () => {
    render(<Button>发送</Button>)
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument()
  })

  it('点击回调可触发', () => {
    let clicked = 0
    render(<Button onClick={() => (clicked += 1)}>停止</Button>)
    fireEvent.click(screen.getByRole('button', { name: '停止' }))
    expect(clicked).toBe(1)
  })
})