import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Sidebar } from '@/components/Sidebar'
import { SoftWarnBanner } from '@/components/SoftWarnBanner'
import { ApiError } from '@/lib/api'
import type { EnvStatus, KbStats, SessionSummary } from '@/lib/types'

const deleteSession = vi.fn()
const createSession = vi.fn()

vi.mock('@/lib/api', () => ({
  deleteSession: (...args: unknown[]) => deleteSession(...args),
  createSession: (...args: unknown[]) => createSession(...args),
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.name = 'ApiError'
      this.status = status
    }
  },
}))

const sessions: SessionSummary[] = [
  { id: 's1', title: '肝癌项目', updated_at: '2026-09-30T00:00:00', message_count: 3 },
  { id: 's2', title: '新会话', updated_at: '2026-09-29T00:00:00', message_count: 0 },
]

const healthyEnv: EnvStatus = {
  rscript: 'C:/R/Rscript.exe',
  kb_path: './knowledge_base',
  kb_ok: true,
  ollama: { state: 'ready', managed: false, detail: '' },
}

const healthyKb: KbStats = { initialized: true }

function renderSidebar(overrides: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  const onSelected = vi.fn()
  const onCreated = vi.fn()
  const onDeleted = vi.fn()
  render(
    <Sidebar
      sessions={sessions}
      activeId="s1"
      kbStats={healthyKb}
      env={healthyEnv}
      onSelect={onSelected}
      onCreated={onCreated}
      onDeleted={onDeleted}
      {...overrides}
    />,
  )
  return { onSelected, onCreated, onDeleted }
}

beforeEach(() => {
  deleteSession.mockReset()
  createSession.mockReset()
  vi.restoreAllMocks()
})

describe('Sidebar', () => {
  it('渲染会话列表并高亮当前会话', () => {
    renderSidebar()
    // 锚定正则：删除按钮 aria-label「删除 <标题>」也含标题，未锚定会命中 2 个
    expect(
      screen.getByRole('button', { name: /^肝癌项目/ }),
    ).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: /^新会话/ })).toBeInTheDocument()
  })

  it('点击会话触发 onSelect', () => {
    const { onSelected } = renderSidebar()
    fireEvent.click(screen.getByRole('button', { name: /^新会话/ }))
    expect(onSelected).toHaveBeenCalledWith('s2')
  })

  it('删除需要二次确认，取消则不调接口', () => {
    renderSidebar()
    fireEvent.click(screen.getByRole('button', { name: '删除 肝癌项目' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(deleteSession).not.toHaveBeenCalled()
  })

  it('确认删除后调接口并通知父级', async () => {
    deleteSession.mockResolvedValue({ deleted: 's1' })
    const { onDeleted } = renderSidebar()
    fireEvent.click(screen.getByRole('button', { name: '删除 肝癌项目' }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(deleteSession).toHaveBeenCalledWith('s1'))
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith('s1'))
  })

  it('删除遇到 409 显示后端 detail 且不移除会话', async () => {
    // 真实 ApiError 实例（来自被 mock 的同一模块），匹配组件的 instanceof 判别
    deleteSession.mockRejectedValue(new ApiError(409, '该会话已有任务在进行'))
    const { onDeleted } = renderSidebar()
    fireEvent.click(screen.getByRole('button', { name: '删除 肝癌项目' }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))

    await waitFor(() =>
      expect(screen.getByText('该会话已有任务在进行')).toBeInTheDocument(),
    )
    expect(screen.getByText('肝癌项目')).toBeInTheDocument()
    expect(onDeleted).not.toHaveBeenCalled()
  })

  it('知识库未初始化 / R 缺失 / 路径缺失 显示红点', () => {
    renderSidebar({
      kbStats: { initialized: false, error: 'kb not ready' },
      env: { rscript: null, kb_path: './knowledge_base', kb_ok: false, ollama: { state: 'ready', managed: false, detail: '' } },
    })
    expect(screen.getByTestId('dot-kb')).toHaveAttribute('data-state', 'error')
    expect(screen.getByTestId('dot-rscript')).toHaveAttribute('data-state', 'error')
    expect(screen.getByTestId('dot-kbpath')).toHaveAttribute('data-state', 'error')
  })

  it('环境健康时显示绿点', () => {
    renderSidebar()
    expect(screen.getByTestId('dot-kb')).toHaveAttribute('data-state', 'ok')
    expect(screen.getByTestId('dot-rscript')).toHaveAttribute('data-state', 'ok')
    expect(screen.getByTestId('dot-kbpath')).toHaveAttribute('data-state', 'ok')
  })

  it('ollama 就绪时绿点', () => {
    renderSidebar()
    expect(screen.getByTestId('dot-ollama')).toHaveAttribute('data-state', 'ok')
  })

  it('ollama 缺嵌入模型（needs_model）红点且原样展示 detail 指引', () => {
    renderSidebar({
      env: {
        ...healthyEnv,
        ollama: {
          state: 'needs_model',
          managed: false,
          detail: '未找到嵌入模型 bge-m3，请手动执行 `ollama pull bge-m3`',
        },
      },
    })
    expect(screen.getByTestId('dot-ollama')).toHaveAttribute('data-state', 'error')
    expect(screen.getByTestId('ollama-needs-model')).toHaveTextContent(
      'ollama pull bge-m3',
    )
    expect(screen.getByTestId('ollama-needs-model')).toHaveTextContent(
      '应用不会自动下载模型',
    )
  })

  it('ollama 不可用（unavailable）显示红点', () => {
    renderSidebar({
      env: { ...healthyEnv, ollama: { state: 'unavailable', managed: false, detail: '' } },
    })
    expect(screen.getByTestId('dot-ollama')).toHaveAttribute('data-state', 'error')
  })
})

describe('SoftWarnBanner', () => {
  it('soft_warn 为 true 时显示提醒', () => {
    render(<SoftWarnBanner visible />)
    expect(screen.getByText(/上下文接近压缩阈值/)).toBeInTheDocument()
  })

  it('soft_warn 为 false 时不渲染', () => {
    render(<SoftWarnBanner visible={false} />)
    expect(screen.queryByText(/上下文接近压缩阈值/)).not.toBeInTheDocument()
  })
})
