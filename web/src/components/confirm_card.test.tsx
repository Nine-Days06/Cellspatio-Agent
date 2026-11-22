import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useReducer } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmCard, type ConfirmCardProps } from '@/components/ConfirmCard'
import {
  confirmReducer,
  initialConfirmState,
  type ConfirmState,
} from '@/lib/confirmState'
import { useChatStore } from '@/store/chat'
import type { DataCardPayload, ScriptCardPayload } from '@/lib/types'

const scriptCard: ScriptCardPayload = {
  message_id: 3,
  script: 'library(DESeq2)\n# 差异表达',
  analysis_type: 'bulk_de',
  params: { contrast: 'tumor_vs_normal' },
  method_context: null,
  user_request: '对 GSE1 做差异表达',
  status: 'pending',
}

const dataCard: DataCardPayload = {
  message_id: 4,
  query: '肝癌',
  message: '找到 2 个候选',
  candidates: [
    {
      source: 'geo',
      asset_id: 'GSE1',
      title: 'HCC RNA-seq',
      asset_type: 'dataset',
      reason: '匹配关键词',
      description: '肝细胞癌组织 RNA-seq',
      metadata: {},
    },
    {
      source: 'geo',
      asset_id: 'GSE2',
      title: 'HCC scRNA-seq',
      asset_type: 'dataset',
      reason: '同疾病',
      description: '',
      metadata: {},
    },
  ],
}

type CardOverrides = Partial<Omit<ConfirmCardProps, 'confirmState'>> & {
  confirmState?: ConfirmState
}

function renderCard(overrides: CardOverrides = {}) {
  const onConfirm = vi.fn()
  const onRegen = vi.fn()
  const view = render(
    <ConfirmCard
      scriptCard={null}
      dataCard={null}
      busy={false}
      confirmState={initialConfirmState}
      onConfirm={onConfirm}
      onRegen={onRegen}
      {...overrides}
    />,
  )
  return { onConfirm, onRegen, ...view }
}

/** 受控 harness：按 ChatPage 语义在点击时派发 submit，failWith 模拟请求失败。 */
function ConfirmHarness({
  failWith,
  onConfirm,
  ...props
}: Omit<ConfirmCardProps, 'confirmState' | 'onConfirm'> & {
  failWith?: string
  onConfirm?: ConfirmCardProps['onConfirm']
}) {
  const [state, dispatch] = useReducer(confirmReducer, initialConfirmState)
  return (
    <ConfirmCard
      {...props}
      confirmState={state}
      onConfirm={(body) => {
        dispatch({ type: 'submit', action: body.action })
        onConfirm?.(body)
        if (failWith) dispatch({ type: 'failure', error: failWith })
      }}
    />
  )
}

function renderHarness(
  overrides: Partial<Omit<ConfirmCardProps, 'confirmState' | 'onConfirm'>> & {
    failWith?: string
  } = {},
) {
  const onConfirm = vi.fn()
  const onRegen = vi.fn()
  const view = render(
    <ConfirmHarness
      scriptCard={null}
      dataCard={null}
      busy={false}
      onConfirm={onConfirm}
      onRegen={onRegen}
      {...overrides}
    />,
  )
  return { onConfirm, onRegen, ...view }
}

// 计划的断言要求 confirm body 带 session_id：组件从会话 store 取，这里给一个可断言的值。
beforeEach(() => {
  useChatStore.setState({ sessionId: 'sess-t7' })
})

describe('脚本确认卡片', () => {
  it('渲染脚本内容与分析类型', () => {
    renderCard({ scriptCard })
    expect(screen.getByText('library(DESeq2)')).toBeInTheDocument()
    expect(screen.getByText('bulk_de')).toBeInTheDocument()
  })

  it('点击执行 → script_confirm（不带 source/asset_id）', () => {
    const { onConfirm } = renderCard({ scriptCard })
    fireEvent.click(screen.getByRole('button', { name: '执行脚本' }))
    expect(onConfirm).toHaveBeenCalledWith({
      session_id: expect.any(String),
      action: 'script_confirm',
      message_id: scriptCard.message_id,
    })
    expect(onConfirm.mock.calls[0][0]).not.toHaveProperty('source')
    expect(onConfirm.mock.calls[0][0]).not.toHaveProperty('asset_id')
  })

  it('点击取消 → script_cancel', () => {
    const { onConfirm } = renderCard({ scriptCard })
    fireEvent.click(screen.getByRole('button', { name: '取消执行' }))
    expect(onConfirm).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'script_cancel' }),
    )
  })

  it('点击重新生成 → onRegen（不发 confirm）', () => {
    const { onConfirm, onRegen } = renderCard({ scriptCard })
    fireEvent.click(screen.getByRole('button', { name: '重新生成' }))
    expect(onRegen).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('约束2/3：busy 时按钮全部 disabled', () => {
    renderCard({ scriptCard, busy: true })
    expect(screen.getByRole('button', { name: '执行脚本' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '取消执行' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重新生成' })).toBeDisabled()
  })

  it('status 非 pending 时隐藏动作按钮，只显示状态', () => {
    renderCard({
      scriptCard: { ...scriptCard, status: 'expired' },
    })
    expect(screen.queryByRole('button', { name: '执行脚本' })).not.toBeInTheDocument()
    expect(screen.getByText(/已过期/)).toBeInTheDocument()
  })

  it('展示确认错误 detail', () => {
    renderCard({
      scriptCard,
      confirmState: { phase: 'failed', action: 'script_confirm', error: 'R 环境不可用' },
    })
    expect(screen.getByText('R 环境不可用')).toBeInTheDocument()
  })

  it('confirm body 携带当前会话 session_id', () => {
    const { onConfirm } = renderCard({ scriptCard })
    fireEvent.click(screen.getByRole('button', { name: '执行脚本' }))
    expect(onConfirm.mock.calls[0][0]).toHaveProperty('session_id', 'sess-t7')
  })

  it('点确认后立即进入已提交态：按钮禁用防重复提交', () => {
    const { onConfirm } = renderHarness({ scriptCard })
    const confirmBtn = screen.getByRole('button', { name: '执行脚本' })
    fireEvent.click(confirmBtn)
    expect(confirmBtn).toBeDisabled()
    expect(screen.getByRole('button', { name: '取消执行' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重新生成' })).toBeDisabled()
    expect(screen.getByText(/已提交/)).toBeInTheDocument()
    fireEvent.click(confirmBtn)
    fireEvent.click(confirmBtn)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('409：卡片保留、显示后端 detail、按钮恢复可点', () => {
    renderHarness({
      scriptCard,
      failWith: '该会话已有任务在进行',
    })
    fireEvent.click(screen.getByRole('button', { name: '执行脚本' }))
    expect(screen.getByTestId('script-card')).toBeInTheDocument()
    expect(screen.getByText('该会话已有任务在进行')).toBeInTheDocument()
    expect(screen.queryByText(/已提交/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '执行脚本' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '取消执行' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '重新生成' })).toBeEnabled()
  })

  it('status=confirmed 时只显示已确认执行，无动作按钮', () => {
    renderCard({ scriptCard: { ...scriptCard, status: 'confirmed' } })
    expect(screen.getByText('已确认执行')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('脚本按纯文本渲染，不注入 DOM 节点', () => {
    const evil: ScriptCardPayload = {
      ...scriptCard,
      script: 'x <- "<script>alert(1)</script>"\n# 注释',
    }
    const { container } = renderCard({ scriptCard: evil })
    expect(container.querySelector('script')).toBeNull()
    expect(container.textContent).toContain('<script>alert(1)</script>')
    expect(container.querySelector('pre')).not.toBeNull()
  })

  it('卡片挂载计划指定的 data-testid', () => {
    renderCard({ scriptCard })
    expect(screen.getByTestId('script-card')).toBeInTheDocument()
    cleanup()
    renderCard({ dataCard })
    expect(screen.getByTestId('data-card')).toBeInTheDocument()
  })
})

describe('数据候选卡片', () => {
  it('列出全部候选', () => {
    renderCard({ dataCard })
    expect(screen.getByText('HCC RNA-seq')).toBeInTheDocument()
    expect(screen.getByText('HCC scRNA-seq')).toBeInTheDocument()
  })

  it('每个候选可确认，带 source/asset_id/query', () => {
    const { onConfirm } = renderCard({ dataCard })
    const buttons = screen.getAllByRole('button', { name: '使用此数据集' })
    expect(buttons).toHaveLength(2)
    fireEvent.click(buttons[1])
    expect(onConfirm).toHaveBeenCalledWith({
      session_id: expect.any(String),
      action: 'data_confirm',
      source: 'geo',
      asset_id: 'GSE2',
      query: '肝癌',
    })
  })

  it('busy 时候选按钮 disabled', () => {
    renderCard({ dataCard, busy: true })
    for (const button of screen.getAllByRole('button', { name: '使用此数据集' })) {
      expect(button).toBeDisabled()
    }
  })

  it('点「使用此数据集」后进入已提交态，防重复提交', () => {
    const { onConfirm } = renderHarness({ dataCard })
    const buttons = screen.getAllByRole('button', { name: '使用此数据集' })
    fireEvent.click(buttons[0])
    expect(buttons[0]).toBeDisabled()
    expect(buttons[1]).toBeDisabled()
    expect(screen.getByText(/已提交/)).toBeInTheDocument()
    fireEvent.click(buttons[1])
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('409：数据卡保留、显示后端 detail、候选按钮恢复可点', () => {
    renderHarness({
      dataCard,
      failWith: '该会话已有任务在进行',
    })
    fireEvent.click(screen.getAllByRole('button', { name: '使用此数据集' })[0])
    expect(screen.getByTestId('data-card')).toBeInTheDocument()
    expect(screen.getByText('该会话已有任务在进行')).toBeInTheDocument()
    expect(screen.queryByText(/已提交/)).not.toBeInTheDocument()
    for (const button of screen.getAllByRole('button', { name: '使用此数据集' })) {
      expect(button).toBeEnabled()
    }
  })
})

describe('空态', () => {
  it('两个卡片都没有时不渲染任何内容', () => {
    const { container } = renderCard()
    expect(container).toBeEmptyDOMElement()
  })
})
