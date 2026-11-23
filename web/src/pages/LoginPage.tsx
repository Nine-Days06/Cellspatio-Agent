import { useState, type FormEvent } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ApiError, login } from '@/lib/api'
import { navigate } from '@/lib/navigation'

export function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setPending(true)
    try {
      await login(username, password)
      navigate('/workspace')
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setError('用户名或密码错误')
      else setError(err instanceof Error ? err.message : '登录失败，请稍后再试')
    } finally {
      setPending(false)
    }
  }

  return (
    <main className="login-page flex min-h-dvh items-center justify-center bg-background px-6 py-12 text-foreground">
      <section className="w-full max-w-sm">
        <button
          type="button"
          className="mb-8 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
          onClick={() => navigate('/')}
        >
          <ArrowLeft className="size-4" />
          返回主页
        </button>
        <h1 className="text-2xl font-semibold tracking-tight">登录管理账户</h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          管理账户由管理员在服务器上创建，没有注册或找回密码入口。
        </p>
        <form className="mt-8 space-y-4" onSubmit={(event) => void handleSubmit(event)}>
          <label className="block text-sm">
            <span className="mb-1.5 block font-medium">用户名</span>
            <Input
              name="username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1.5 block font-medium">密码</span>
            <Input
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button className="w-full" size="lg" type="submit" disabled={pending}>
            {pending ? '正在登录…' : '登录'}
          </Button>
        </form>
      </section>
    </main>
  )
}
