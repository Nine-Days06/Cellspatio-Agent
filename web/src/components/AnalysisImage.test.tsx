import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AnalysisImage } from '@/components/AnalysisImage'

describe('AnalysisImage', () => {
  it('打开同一原图，Esc 关闭并恢复焦点', async () => {
    render(<AnalysisImage src="/diagnostic.png" alt="科学未复核的点图" />)
    const trigger = screen.getByRole('button', { name: '查看原始尺寸：科学未复核的点图' })
    trigger.focus()
    fireEvent.click(trigger)
    const dialog = await screen.findByRole('dialog', { name: '原始尺寸图表' })
    expect(within(dialog).getByRole('img')).toHaveAttribute('src', '/diagnostic.png')
    expect(within(dialog).getByRole('region')).toHaveAttribute('tabindex', '0')
    fireEvent.keyDown(dialog, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
  })

  it('图像失败显示读取方向，换来源后恢复查看入口', () => {
    const { rerender } = render(<AnalysisImage src="/bad.png" alt="诊断图" />)
    fireEvent.error(screen.getByRole('img'))
    expect(screen.getByRole('alert')).toHaveTextContent('只读重试')
    expect(screen.queryByRole('button', { name: '查看原始尺寸：诊断图' })).not.toBeInTheDocument()
    rerender(<AnalysisImage src="/next.png" alt="诊断图" />)
    expect(screen.getByRole('button', { name: '查看原始尺寸：诊断图' })).toBeInTheDocument()
  })

  it('同一来源重试会重建图像，但不改变 URL', () => {
    render(<AnalysisImage src="/exact-attempt.png" alt="诊断图" />)
    const original = screen.getByRole('img')
    fireEvent.error(original)
    fireEvent.click(screen.getByRole('button', { name: '重试图像' }))
    expect(screen.getByRole('img')).not.toBe(original)
    expect(screen.getByRole('img')).toHaveAttribute('src', '/exact-attempt.png')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('原图失败仍保留关闭入口，并可在弹窗内重试', async () => {
    render(<AnalysisImage src="/exact-attempt.png" alt="诊断图" />)
    fireEvent.click(screen.getByRole('button', { name: '查看原始尺寸：诊断图' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.error(within(dialog).getByRole('img'))
    expect(within(dialog).getByRole('button', { name: '关闭图表' })).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '重试原图' }))
    expect(within(dialog).getByRole('img')).toHaveAttribute('src', '/exact-attempt.png')
  })
})
