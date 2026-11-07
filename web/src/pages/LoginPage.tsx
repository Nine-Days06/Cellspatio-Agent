/** 登录页：账号密码 → 会话 cookie；成功后由 App 重新拉取 /api/auth/me。 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, login } from '@/lib/api'

export function LoginPage() {
  const queryClient = useQueryClient()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
      await queryClient.invalidateQueries({ queryKey: ['me'] })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '登录失败')
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl border border-border bg-card p-8 shadow-sm"
      >
        <h1 className="text-xl font-semibold tracking-tight">CellSpatio</h1>
        <p className="mt-1 text-sm text-muted-foreground">请使用分配的账号登录</p>
        <label className="mt-6 block text-sm">
          用户名
          <input
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label className="mt-4 block text-sm">
          密码
          <input
            type="password"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error && <p className="mt-4 text-sm text-destructive">{error}</p>}
        <button
          type="submit"
          disabled={busy}
          className="mt-6 w-full rounded-md bg-primary px-3 py-2 text-primary-foreground disabled:opacity-60"
        >
          {busy ? '登录中…' : '登录'}
        </button>
      </form>
    </main>
  )
}
