import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import { HomePage } from '@/pages/HomePage'

const fetchWorkspaceMock = vi.fn()
const startNewWorkspaceMock = vi.fn()
const listSessionsMock = vi.fn()
const listProjectsMock = vi.fn()
const logoutMock = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    fetchWorkspace: (...args: unknown[]) => fetchWorkspaceMock(...args),
    startNewWorkspace: (...args: unknown[]) => startNewWorkspaceMock(...args),
    listSessions: (...args: unknown[]) => listSessionsMock(...args),
    listProjects: (...args: unknown[]) => listProjectsMock(...args),
    logout: (...args: unknown[]) => logoutMock(...args),
  }
})

function workspace(overrides: Record<string, unknown> = {}) {
  return {
    workspace: {
      id: 'ws-1',
      kind: 'ephemeral',
      label: '临时体验空间',
      created_at: '2026-11-23T00:00:00Z',
      expires_at: '2026-11-24T00:00:00Z',
      remaining_seconds: 3600,
      expired: false,
      ...overrides,
    },
  }
}

beforeEach(() => {
  window.history.replaceState({}, '', '/')
  fetchWorkspaceMock.mockReset()
  startNewWorkspaceMock.mockReset()
  listSessionsMock.mockReset().mockResolvedValue({ sessions: [] })
  listProjectsMock.mockReset().mockResolvedValue({ projects: [] })
  logoutMock.mockReset().mockResolvedValue({ ok: true })
})

afterEach(() => {
  window.history.replaceState({}, '', '/')
})

describe('HomePage', () => {
  it('renders the project introduction and three entries', () => {
    render(<HomePage />)
    expect(screen.getByText(/从单细胞数据到可复核的分析记录/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '试用' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /工作区/ })).toBeInTheDocument()
  })

  it('opens the login page', () => {
    render(<HomePage />)
    fireEvent.click(screen.getByRole('button', { name: '登录' }))
    expect(window.location.pathname).toBe('/login')
  })

  it('creates a fresh trial workspace and enters it', async () => {
    fetchWorkspaceMock.mockResolvedValue(workspace())
    startNewWorkspaceMock.mockResolvedValue(workspace({ id: 'ws-2' }))
    render(<HomePage />)

    fireEvent.click(screen.getByRole('button', { name: '试用' }))

    await waitFor(() => expect(window.location.pathname).toBe('/workspace'))
    expect(startNewWorkspaceMock).toHaveBeenCalled()
  })

  it('asks before discarding temporary data', async () => {
    fetchWorkspaceMock.mockResolvedValue(workspace())
    listSessionsMock.mockResolvedValue({
      sessions: [{ id: 's1', title: '已有会话', updated_at: '', message_count: 1, project_id: null, pinned: false, archived_at: null }],
    })
    startNewWorkspaceMock.mockResolvedValue(workspace({ id: 'ws-2' }))
    render(<HomePage />)

    fireEvent.click(screen.getByRole('button', { name: '试用' }))

    expect(await screen.findByText('放弃当前临时数据？')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '放弃并新建' }))
    await waitFor(() => expect(window.location.pathname).toBe('/workspace'))
  })

  it('enters the current workspace when one exists', async () => {
    fetchWorkspaceMock.mockResolvedValue(workspace({ kind: 'persistent', id: 'admin-ws' }))
    render(<HomePage />)

    fireEvent.click(screen.getByRole('button', { name: /工作区/ }))

    await waitFor(() => expect(window.location.pathname).toBe('/workspace'))
    expect(startNewWorkspaceMock).not.toHaveBeenCalled()
  })

  it('creates a trial workspace when none is available', async () => {
    fetchWorkspaceMock.mockRejectedValue(new ApiError(410, '已到期'))
    startNewWorkspaceMock.mockResolvedValue(workspace({ id: 'ws-2' }))
    render(<HomePage />)

    fireEvent.click(screen.getByRole('button', { name: /工作区/ }))

    await waitFor(() => expect(window.location.pathname).toBe('/workspace'))
    expect(startNewWorkspaceMock).toHaveBeenCalled()
  })
})
