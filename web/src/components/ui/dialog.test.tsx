import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'

function Harness() {
  const [open, setOpen] = useState(false)
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger>删除</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>确认删除</DialogTitle>
          <DialogDescription>删除后不可恢复。</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose>取消</DialogClose>
          <Button variant="destructive">确认</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

describe('Dialog', () => {
  it('默认不渲染内容，点击触发后渲染', () => {
    render(<Harness />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('确认删除')).toBeInTheDocument()
  })

  it('点击取消关闭', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('弹窗与遮罩统一 160ms、0.98 缩放，居中位移独立于动画', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: '删除' }))

    const dialog = screen.getByRole('dialog')
    expect(dialog.className).toContain('data-open:zoom-in-98')
    expect(dialog.className).not.toContain('zoom-in-95')
    expect(dialog.className).toContain('-translate-x-1/2')
    expect(dialog.className).toContain('-translate-y-1/2')

    const overlay = document.querySelector('[data-slot="dialog-overlay"]')
    expect(overlay?.className).toContain('data-open:fade-in-0')
  })
})
