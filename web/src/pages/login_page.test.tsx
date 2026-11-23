import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import { LoginPage } from '@/pages/LoginPage'

const loginMock = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    login: (...args: unknown[]) => loginMock(...args),
  }
})

beforeEach(() => {
  window.history.replaceState({}, '', '/login')
  loginMock.mockReset()
})

afterEach(() => {
  window.history.replaceState({}, '', '/')
})

function submit(username: string, password: string) {
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: username } })
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: password } })
  fireEvent.click(screen.getByRole('button', { name: '登录' }))
}

describe('LoginPage', () => {
  it('enters the workspace after a successful login', async () => {
    loginMock.mockResolvedValue({
      workspace: { id: 'ws', kind: 'persistent', label: 'admin1', expired: false },
      account: { username: 'admin1' },
    })
    render(<LoginPage />)

    submit('admin1', 'secret')

    await waitFor(() => expect(window.location.pathname).toBe('/workspace'))
    expect(loginMock).toHaveBeenCalledWith('admin1', 'secret')
  })

  it('shows a stable error on 401', async () => {
    loginMock.mockRejectedValue(new ApiError(401, '用户名或密码错误'))
    render(<LoginPage />)

    submit('admin1', 'wrong')

    expect(await screen.findByRole('alert')).toHaveTextContent('用户名或密码错误')
    expect(window.location.pathname).toBe('/login')
  })

  it('returns to the home page', () => {
    render(<LoginPage />)
    fireEvent.click(screen.getByRole('button', { name: /返回主页/ }))
    expect(window.location.pathname).toBe('/')
  })
})
