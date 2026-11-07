import { useQuery } from '@tanstack/react-query'
import { ApiError, fetchMe } from './lib/api'
import { ChatPage } from './pages/ChatPage'
import { LoginPage } from './pages/LoginPage'

export default function App() {
  const { data, error, isLoading } = useQuery({
    queryKey: ['me'],
    queryFn: fetchMe,
    retry: false,
    refetchOnWindowFocus: false,
  })

  if (isLoading) return <main className="min-h-screen bg-background" />
  if (error) {
    if (error instanceof ApiError && error.status === 401) return <LoginPage />
    return (
      <main className="flex min-h-screen items-center justify-center px-6 text-sm text-destructive">
        无法连接服务：{error instanceof Error ? error.message : '未知错误'}
      </main>
    )
  }
  return <ChatPage user={data?.user} />
}
