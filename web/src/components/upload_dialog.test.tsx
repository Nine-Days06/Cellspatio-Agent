import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { UploadDialog } from '@/components/UploadDialog'

function chooseFile(): void {
  const input = screen.getByLabelText('选择上传文件')
  fireEvent.change(input, {
    target: { files: [new File(['counts'], 'counts.csv', { type: 'text/csv' })] },
  })
}

async function openDialog(): Promise<void> {
  fireEvent.click(screen.getByRole('button', { name: '上传数据' }))
}

describe('UploadDialog 上传反馈', () => {
  it('提交期间 aria-busy、进行条与按钮文案都在，且不出现百分比', async () => {
    let resolveUpload = (): void => {}
    const onSubmit = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveUpload = resolve
        }),
    )
    render(<UploadDialog onSubmit={onSubmit} />)
    await openDialog()
    chooseFile()

    fireEvent.click(screen.getByRole('button', { name: '上传并运行 QC' }))

    const dialog = screen.getByRole('dialog')
    await waitFor(() => expect(dialog).toHaveAttribute('aria-busy', 'true'))
    expect(screen.getByRole('button', { name: '正在上传…' })).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: '上传进行中' })).toBeInTheDocument()
    expect(screen.getByText('正在上传，无法预估剩余进度')).toBeInTheDocument()
    expect(screen.queryByText(/%/)).toBeNull()

    resolveUpload?.()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('失败时提示为 role=alert 并保持可读', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('文件格式不受支持'))
    render(<UploadDialog onSubmit={onSubmit} />)
    await openDialog()
    chooseFile()

    fireEvent.click(screen.getByRole('button', { name: '上传并运行 QC' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('文件格式不受支持')
    expect(alert.className).toContain('motion-fade-fast')
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
