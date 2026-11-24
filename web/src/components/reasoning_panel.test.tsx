import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { ReasoningPanel, type ReasoningPanelProps } from '@/components/ReasoningPanel'

function Controlled(props: Omit<ReasoningPanelProps, 'expanded' | 'onToggle'>) {
  const [expanded, setExpanded] = useState(false)
  return (
    <ReasoningPanel
      {...props}
      expanded={expanded}
      onToggle={() => setExpanded((value) => !value)}
    />
  )
}

describe('ReasoningPanel', () => {
  it('没有思考内容时不渲染', () => {
    const { container } = render(<Controlled text="   " running={false} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('进行中显示动态指示、实时字数与末尾预览', () => {
    render(
      <Controlled text={'开头' + '甲'.repeat(40) + '末尾标记'} running startedAt={Date.now() - 3000} />,
    )

    expect(screen.getByText('思考中')).toBeInTheDocument()
    expect(screen.getByText('46 字')).toBeInTheDocument()
    expect(screen.getByText(/末尾标记/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '展开思考过程' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
  })

  it('结束后显示思考耗时并可展开回看', () => {
    render(<Controlled text="完整思考内容" running={false} elapsedMs={4000} />)

    expect(screen.getByText('已思考 4 秒')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开思考过程' }))

    expect(screen.getByText('完整思考内容')).toBeInTheDocument()
    expect(screen.getByText('模型自述，不是证据，可能不准确。')).toBeInTheDocument()
  })

  it('展开状态由调用方持有，重渲染后保持用户选择', () => {
    const { rerender } = render(<Controlled text="思考" running={false} />)
    fireEvent.click(screen.getByRole('button', { name: '展开思考过程' }))

    rerender(<Controlled text="思考" running={false} />)

    expect(screen.getByRole('button', { name: '收起思考过程' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
  })

  it('超限时标记截断并在展开区说明', () => {
    render(<Controlled text="思考内容" running={false} truncated />)

    expect(screen.getByText('已截断')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开思考过程' }))
    expect(screen.getByText(/思考内容超过上限/)).toBeInTheDocument()
  })
})
