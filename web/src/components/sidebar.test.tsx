import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Sidebar } from '@/components/Sidebar'
import { SoftWarnBanner } from '@/components/SoftWarnBanner'
import { ApiError } from '@/lib/api'
import type { EnvStatus, KbStats, Project, SessionSummary } from '@/lib/types'

const deleteSession = vi.fn()
const createSession = vi.fn()
const createProject = vi.fn()
const deleteProject = vi.fn()
const updateProject = vi.fn()
const updateSession = vi.fn()

vi.mock('@/lib/api', () => ({
  deleteSession: (...args: unknown[]) => deleteSession(...args),
  createSession: (...args: unknown[]) => createSession(...args),
  createProject: (...args: unknown[]) => createProject(...args),
  deleteProject: (...args: unknown[]) => deleteProject(...args),
  updateProject: (...args: unknown[]) => updateProject(...args),
  updateSession: (...args: unknown[]) => updateSession(...args),
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
  {
    id: 's1',
    title: '肝癌项目',
    updated_at: '2026-09-30T00:00:00',
    message_count: 3,
    project_id: null,
    pinned: false,
    archived_at: null,
  },
  {
    id: 's2',
    title: '新会话',
    updated_at: '2026-09-29T00:00:00',
    message_count: 0,
    project_id: null,
    pinned: false,
    archived_at: null,
  },
]

const healthyEnv: EnvStatus = {
  rscript: 'C:/R/Rscript.exe',
  kb_path: './knowledge_base',
  kb_ok: true,
  ollama: { state: 'ready', managed: false, detail: '' },
  ollama_start_mode: 'lazy',
}

const healthyKb: KbStats = { initialized: true }

const projects: Project[] = [
  {
    id: 'p1',
    label: '肝项目组',
    kind: 'local',
    root_path: null,
    created_at: '2026-09-30T00:00:00',
    updated_at: '2026-09-30T00:00:00',
    archived_at: null,
    session_count: 1,
  },
]

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
  createProject.mockReset()
  deleteProject.mockReset()
  updateProject.mockReset()
  updateSession.mockReset()
  vi.restoreAllMocks()
})

describe('Sidebar', () => {
  it('搜索只筛选本地会话，清空后保留原选中与全部列表', () => {
    const { onSelected } = renderSidebar()
    const search = screen.getByRole('searchbox', { name: '搜索会话' })
    fireEvent.change(search, { target: { value: '肝癌' } })
    expect(screen.getByRole('button', { name: /^肝癌项目/ })).toHaveAttribute('aria-current', 'true')
    expect(screen.queryByRole('button', { name: /^新会话/ })).not.toBeInTheDocument()
    fireEvent.change(search, { target: { value: '不存在' } })
    expect(screen.getByRole('status')).toHaveTextContent('没有匹配的会话')
    fireEvent.change(search, { target: { value: '' } })
    expect(screen.getByRole('button', { name: /^新会话/ })).toBeInTheDocument()
    expect(onSelected).not.toHaveBeenCalled()
    expect(createSession).not.toHaveBeenCalled()
    expect(deleteSession).not.toHaveBeenCalled()
  })
  it('移动会话导航选择后关闭，删除仍须独立二次确认', () => {
    const { onSelected } = renderSidebar()
    fireEvent.click(screen.getByRole('button', { name: '会话' }))
    const drawer = screen.getByRole('dialog', { name: '会话导航' })
    fireEvent.click(within(drawer).getByRole('button', { name: '删除 肝癌项目' }))
    expect(screen.getByRole('dialog', { name: '确认删除会话' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(deleteSession).not.toHaveBeenCalled()
    fireEvent.click(within(drawer).getByRole('button', { name: /^新会话/ }))
    expect(onSelected).toHaveBeenCalledWith('s2')
    expect(screen.queryByRole('dialog', { name: '会话导航' })).not.toBeInTheDocument()
  })
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
      env: { rscript: null, kb_path: './knowledge_base', kb_ok: false, ollama: { state: 'ready', managed: false, detail: '' }, ollama_start_mode: 'lazy' },
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
    expect(screen.getByTestId('ollama-needs-model')).toHaveClass('text-[color:var(--review)]')
  })

  it('ollama 不可用（unavailable）显示红点', () => {
    renderSidebar({
      env: { ...healthyEnv, ollama: { state: 'unavailable', managed: false, detail: '' } },
    })
    expect(screen.getByTestId('dot-ollama')).toHaveAttribute('data-state', 'error')
  })

  it('启动模式为 eager 时只读展示「随程序启动」', () => {
    renderSidebar({ env: { ...healthyEnv, ollama_start_mode: 'eager' } })
    expect(screen.getByTestId('ollama-start-mode')).toHaveTextContent('随程序启动')
  })

  it('启动模式为 lazy 时只读展示「按需唤起」', () => {
    renderSidebar({ env: { ...healthyEnv, ollama_start_mode: 'lazy' } })
    expect(screen.getByTestId('ollama-start-mode')).toHaveTextContent('按需唤起')
  })

  it('启动模式只读：无任何可点击的切换控件', () => {
    renderSidebar({ env: { ...healthyEnv, ollama_start_mode: 'eager' } })
    // 只读展示：不得出现按钮/开关，避免用户误以为点一下就能改
    expect(screen.queryByRole('button', { name: /启动模式|随程序启动/ })).toBeNull()
  })
})

it('按项目分组渲染会话，项目头可新建会话', () => {
  createSession.mockResolvedValue({
    session: {
      id: 's3',
      title: '新会话',
      created_at: '2026-10-01T00:00:00',
      updated_at: '2026-10-01T00:00:00',
      summary: null,
      summary_upto: 0,
      downloaded_assets: [],
      project_id: 'p1',
      pinned: false,
      archived_at: null,
    },
  })
  const { onCreated } = renderSidebar({
    projects,
    sessions: [{ ...sessions[0], project_id: 'p1' }],
  })
  expect(screen.getByRole('button', { name: /^肝项目组/ })).toHaveAttribute('aria-expanded', 'true')
  fireEvent.click(screen.getByRole('button', { name: '在 肝项目组 新建会话' }))
  return waitFor(() => {
    expect(createSession).toHaveBeenCalledWith(undefined, 'p1')
    expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ id: 's3', project_id: 'p1' }))
  })
})

it('新建项目：填名后调用 createProject 并刷新', async () => {
  createProject.mockResolvedValue({ project: projects[0] })
  const onChanged = vi.fn()
  renderSidebar({ projects: [], sessions: [], onChanged })
  fireEvent.click(screen.getByRole('button', { name: '新建项目' }))
  fireEvent.change(screen.getByLabelText('新项目名'), { target: { value: ' 肝项目组 ' } })
  fireEvent.click(screen.getByRole('button', { name: '创建项目' }))
  await waitFor(() => expect(createProject).toHaveBeenCalledWith('肝项目组', null))
  await waitFor(() => expect(onChanged).toHaveBeenCalled())
})

it('会话菜单可移动与归档', async () => {
  updateSession.mockResolvedValue({ session: {} })
  const onChanged = vi.fn()
  const onArchived = vi.fn()
  renderSidebar({ projects, onChanged, onArchived })
  fireEvent.click(screen.getByRole('button', { name: '更多 新会话' }))
  fireEvent.change(screen.getByLabelText('移动到项目'), { target: { value: 'p1' } })
  fireEvent.click(screen.getByRole('button', { name: '移动' }))
  await waitFor(() => expect(updateSession).toHaveBeenCalledWith('s2', { project_id: 'p1' }))
  await waitFor(() => expect(onChanged).toHaveBeenCalled())

  fireEvent.click(screen.getByRole('button', { name: '更多 新会话' }))
  fireEvent.click(screen.getByRole('button', { name: '归档会话' }))
  await waitFor(() => expect(onArchived).toHaveBeenCalledWith('s2'))
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
