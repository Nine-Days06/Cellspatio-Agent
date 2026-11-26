import { beforeEach, describe, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { RerankSetting } from '@/components/RerankSetting'
import { RERANK_KEY } from '@/lib/preferences'

beforeEach(() => {
  localStorage.clear()
})

describe('RerankSetting', () => {
  it('默认关闭，开启后持久化并显示当前模型', () => {
    render(<RerankSetting configured model="BAAI/bge-reranker-v2-m3" />)
    fireEvent.click(screen.getByRole('button', { name: '检索设置' }))

    const toggle = screen.getByRole('switch', { name: '文献重排' })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByText(/当前模型：BAAI\/bge-reranker-v2-m3/)).toBeInTheDocument()

    fireEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-checked', 'true')
    expect(localStorage.getItem(RERANK_KEY)).toBe('1')
  })

  it('从本地 "1" 恢复开启状态', () => {
    localStorage.setItem(RERANK_KEY, '1')

    render(<RerankSetting configured model="m" />)
    fireEvent.click(screen.getByRole('button', { name: '检索设置' }))

    expect(screen.getByRole('switch', { name: '文献重排' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
  })

  it('未配置时开关禁用并说明原因', () => {
    render(<RerankSetting configured={false} model="" />)
    fireEvent.click(screen.getByRole('button', { name: '检索设置' }))

    expect(screen.getByRole('switch', { name: '文献重排' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('当前部署未配置重排服务')
  })

  it('未配置但存量开启时显示不可用而不是已开启', () => {
    localStorage.setItem(RERANK_KEY, '1')

    render(<RerankSetting configured={false} model="" />)
    fireEvent.click(screen.getByRole('button', { name: '检索设置' }))

    expect(screen.getByText('当前部署不可用')).toBeInTheDocument()
  })

  it('说明开启与关闭的区别', () => {
    render(<RerankSetting configured model="m" />)
    fireEvent.click(screen.getByRole('button', { name: '检索设置' }))

    expect(screen.getByText(/相关文献更可能靠前/)).toBeInTheDocument()
    expect(screen.getByText(/不调用重排模型，无额外延迟/)).toBeInTheDocument()
  })

  it('Escape 关闭弹窗并把焦点还给入口', async () => {
    render(<RerankSetting configured model="m" />)
    const trigger = screen.getByRole('button', { name: '检索设置' })
    trigger.focus()
    fireEvent.click(trigger)
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
  })
})
