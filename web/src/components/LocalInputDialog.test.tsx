import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LocalInputDialog } from '@/components/LocalInputDialog'
import { ApiError } from '@/lib/api'

function openDialog() {
  fireEvent.click(screen.getByRole('button', { name: '本地数据' }))
}

describe('LocalInputDialog', () => {
  it('用项目工作目录预填输入框', () => {
    render(<LocalInputDialog onSubmit={vi.fn()} defaultPath="D:/data/project" />)

    openDialog()

    expect(screen.getByLabelText('本地数据目录')).toHaveValue('D:/data/project')
  })

  it('切换项目后重新打开会刷新预填路径', () => {
    const { rerender } = render(
      <LocalInputDialog onSubmit={vi.fn()} defaultPath="D:/data/a" />,
    )
    openDialog()
    expect(screen.getByLabelText('本地数据目录')).toHaveValue('D:/data/a')
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    rerender(<LocalInputDialog onSubmit={vi.fn()} defaultPath="D:/data/b" />)
    openDialog()
    expect(screen.getByLabelText('本地数据目录')).toHaveValue('D:/data/b')
  })

  it('提交去空格后的路径并在成功后关闭', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<LocalInputDialog onSubmit={onSubmit} />)

    openDialog()
    const field = screen.getByLabelText('本地数据目录')
    fireEvent.change(field, {
      target: { value: '  D:/data/pbmc3k/hg19  ' },
    })
    await waitFor(() => expect(field).toHaveValue('  D:/data/pbmc3k/hg19  '))
    fireEvent.click(screen.getByRole('button', { name: '开始 QC' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('D:/data/pbmc3k/hg19'))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('失败时展示后端 detail 并保留对话框', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new ApiError(400, '路径不存在：D:/missing'))
    render(<LocalInputDialog onSubmit={onSubmit} />)

    openDialog()
    const field = screen.getByLabelText('本地数据目录')
    fireEvent.change(field, {
      target: { value: 'D:/missing' },
    })
    await waitFor(() => expect(field).toHaveValue('D:/missing'))
    fireEvent.click(screen.getByRole('button', { name: '开始 QC' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('路径不存在：D:/missing')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始 QC' })).toBeEnabled()
  })

  it('运行中禁用提交并提示 QC 正在执行', async () => {
    let finish: () => void = () => {}
    const onSubmit = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve
        }),
    )
    render(<LocalInputDialog onSubmit={onSubmit} />)

    openDialog()
    const field = screen.getByLabelText('本地数据目录')
    fireEvent.change(field, {
      target: { value: 'D:/data' },
    })
    await waitFor(() => expect(field).toHaveValue('D:/data'))
    fireEvent.click(screen.getByRole('button', { name: '开始 QC' }))

    expect(await screen.findByRole('button', { name: '正在运行 QC…' })).toBeDisabled()
    finish()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})
